"""Cheap local stability hints. These do not establish user intent or identity."""

import base64
from collections import deque
import io

import numpy as np
from PIL import Image


def fingerprint(image):
    with Image.open(io.BytesIO(base64.b64decode(image))) as source:
        pixels = np.asarray(source.convert("RGB").resize((32, 24)), dtype=np.float32) / 255
    # Channel means avoid hard histogram bins which jump under auto-exposure.
    colour = pixels.mean(axis=(0, 1))
    colour -= colour.mean()
    return pixels, colour


def movement(left, right):
    a, ac = left
    b, bc = right
    # Estimate camera translation, allowing a generous hand-held range. Colour
    # and residual changes detect cuts; accumulated direction detects a sweep.
    exposure = float(np.clip(np.median(a.mean(axis=2)) - np.median(b.mean(axis=2)), -.15, .15))
    reference = a[4:-4, 5:-5] - exposure
    candidates = []
    for dy in range(-4, 5):
        for dx in range(-5, 6):
            error = float(np.abs(reference - b[4 + dy:20 + dy, 5 + dx:27 + dx]).mean())
            candidates.append((error + .0005 * (abs(dx) + abs(dy)), error, dx, dy))
    _, residual, dx, dy = min(candidates)
    unshifted = float(np.abs(reference - b[4:-4, 5:-5]).mean())
    # Flat or changing content cannot provide a trustworthy direction vector.
    if unshifted - residual < .003 or residual > .14:
        dx = dy = 0
    return max(residual, float(np.abs(ac - bc).max())), -dx, -dy


def distance(left, right):
    return movement(left, right)[0]


class PresentationFilter:
    def __init__(self, threshold=0.07):
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.scene = getattr(self, "scene", 0) + 1
        self.previous = None
        self.since = self.last_at = None
        self.moving = False
        self.steps = deque(maxlen=12)
        self.metrics = {}

    def update(self, signature, received_at):
        step, dx, dy = movement(signature, self.previous) if self.previous is not None else (0, 0, 0)
        reason = None
        net = travel = coherence = 0
        if self.previous is None or received_at - self.last_at > 1:
            reason = "first_or_gap"
        elif step >= max(.18, self.threshold * 2.5):
            reason = "scene_cut"
        else:
            self.steps.append((received_at, dx, dy, step))
            while self.steps and received_at - self.steps[0][0] > 2:
                self.steps.popleft()
            net = float(np.hypot(sum(row[1] for row in self.steps), sum(row[2] for row in self.steps)))
            travel = sum(float(np.hypot(row[1], row[2])) for row in self.steps)
            coherence = net / travel if travel else 0
            span = received_at - self.steps[0][0]
            changing = sum(row[3] > max(.13, self.threshold * 1.8) for row in self.steps) / len(self.steps)
            recent = [row for row in self.steps if received_at - row[0] <= .75]
            stopped = (len(recent) >= 3 and recent[-1][0] - recent[0][0] >= .4
                       and sum(float(np.hypot(row[1], row[2])) for row in recent) <= 1
                       and max(row[3] for row in recent) <= .035)
            # A bounded/reversing wobble is a dwell, even when no individual
            # frame is motionless. Only persistent travel/churn is a sweep.
            sweeping = not stopped and span >= .9 and ((net >= 8 and coherence >= .75) or changing >= .7)
            if sweeping:
                if not self.moving:
                    self.scene += 1
                    reason = "camera_sweep"
                self.since = received_at
            elif self.moving:
                self.since = recent[0][0] if stopped else received_at
                reason = "dwell_started"
            self.moving = sweeping
        if reason in {"first_or_gap", "scene_cut"}:
            self.scene += 1
            self.since = received_at
            self.steps.clear()
            self.moving = False
        self.previous, self.last_at = signature, received_at
        stable = max(0, received_at - self.since)
        self.metrics = {"scene_id": self.scene, "stable_seconds": round(stable, 2),
                        "motion_state": "moving" if self.moving else "dwelling",
                        "step_difference": round(step, 3), "window_net_pixels": round(net, 2),
                        "window_travel_pixels": round(travel, 2), "direction_coherence": round(coherence, 2),
                        "change_reason": reason}
        return self.scene, stable
