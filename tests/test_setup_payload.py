import unittest

from src.web_server import SetupPayload


class SetupPayloadTests(unittest.TestCase):
    def test_setup_accepts_browser_camera_preference(self) -> None:
        payload = SetupPayload(
            camera_device_id="usb-camera-id",
            camera_device_label="UGREEN Camera 2K",
            focus_points=[[0.1, 0.1], [0.9, 0.9]],
            focus_enabled=True,
            line_points=[[0.1, 0.5], [0.9, 0.5]],
            door_points=[],
            door_enabled=False,
        )

        self.assertEqual(payload.camera_device_id, "usb-camera-id")
        self.assertEqual(payload.camera_device_label, "UGREEN Camera 2K")


if __name__ == "__main__":
    unittest.main()
