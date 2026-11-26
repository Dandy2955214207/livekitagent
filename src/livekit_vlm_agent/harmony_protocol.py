"""Shared media framing and validation for the HarmonyOS gateways."""

from io import BytesIO
import hmac
import json
import struct

MIC = 0x01
CAMERA = 0x02
AI_AUDIO = 0x11
INPUT_SAMPLES = 320
JPEG_LIMIT = 160 * 1024
OFFICIAL_CONTROLS = frozenset({'scene_control', 'requirement_control', 'task_stop', 'task_repeat'})


class GatewayError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def valid_uint(value, minimum=0):
    return type(value) is int and minimum <= value <= 0xffffffff


def audio_packet(output_id, sequence, pcm):
    return struct.pack('!BII', AI_AUDIO, output_id, sequence) + pcm


def encode_agent_event(event, key, topic):
    body = json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    signature = hmac.digest(key, topic.encode('ascii') + b'\n' + body, 'sha256').hex().encode('ascii')
    return signature + b'.' + body


def decode_agent_event(packet, key, topic):
    signature, _, body = packet.partition(b'.')
    expected = hmac.digest(key, topic.encode('ascii') + b'\n' + body, 'sha256').hex().encode('ascii')
    if not hmac.compare_digest(signature, expected):
        return None
    return json.loads(body)


def decode_jpeg(data, *, preserve_size=False):
    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(data), formats=('JPEG',)) as image:
            if not (1 <= image.width <= 640 and 1 <= image.height <= 640):
                raise GatewayError('invalid_camera_frame')
            if preserve_size:
                return image.width, image.height, image.convert('RGB').tobytes()
            # Keep a fixed RTP resolution when the phone rotates or swaps cameras.
            return ImageOps.pad(image.convert('RGB'), (640, 640)).tobytes()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise GatewayError('invalid_camera_frame') from exc
