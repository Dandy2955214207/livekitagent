"""Shared media framing and validation for the HarmonyOS gateways."""

from io import BytesIO
import struct

MIC = 0x01
CAMERA = 0x02
AI_AUDIO = 0x11
INPUT_SAMPLES = 320
JPEG_LIMIT = 160 * 1024


class GatewayError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def valid_uint(value, minimum=0):
    return type(value) is int and minimum <= value <= 0xffffffff


def audio_packet(output_id, sequence, pcm):
    return struct.pack('!BII', AI_AUDIO, output_id, sequence) + pcm


def decode_jpeg(data):
    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(data), formats=('JPEG',)) as image:
            if not (1 <= image.width <= 640 and 1 <= image.height <= 640):
                raise GatewayError('invalid_camera_frame')
            # Keep a fixed RTP resolution when the phone rotates or swaps cameras.
            return ImageOps.pad(image.convert('RGB'), (640, 640)).tobytes()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise GatewayError('invalid_camera_frame') from exc
