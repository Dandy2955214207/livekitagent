// Expire queued visual announcements before they turn into stale scene claims.
// Playback already started is left to the official interruption/receipt logic.
export class DeliveryWindow {
  constructor(expire, ttlMs = 8000) { this.expire = expire; this.ttlMs = ttlMs; this.pending = new Map(); }
  add(delivery) {
    const timer = setTimeout(() => {
      this.remove(delivery); this.expire(delivery, 'evidence_delivery_expired');
    }, this.ttlMs);
    timer.unref?.(); this.pending.set(delivery.deliveryId, { delivery, timer });
  }
  remove(delivery) {
    const current = this.pending.get(delivery.deliveryId);
    if (current) clearTimeout(current.timer);
    this.pending.delete(delivery.deliveryId);
  }
  invalidateVisual() {
    for (const { delivery } of [...this.pending.values()]) {
      this.remove(delivery); this.expire(delivery, 'visual_source_changed');
    }
  }
  close() { for (const { timer } of this.pending.values()) clearTimeout(timer); this.pending.clear(); }
}
