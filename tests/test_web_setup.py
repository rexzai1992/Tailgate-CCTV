import unittest

from src.web_server import WebCameraProcessor, frigate_restream_url


class WebSetupTests(unittest.TestCase):
    def test_focus_corners_are_sorted(self) -> None:
        points = WebCameraProcessor._validated_focus(
            [[0.8, 0.9], [0.2, 0.1]]
        )
        self.assertEqual(points, [[0.2, 0.1], [0.8, 0.9]])

    def test_focus_area_rejects_tiny_box(self) -> None:
        with self.assertRaises(ValueError):
            WebCameraProcessor._validated_focus(
                [[0.2, 0.2], [0.21, 0.21]]
            )

    def test_focus_polygon_is_kept_as_is(self) -> None:
        polygon = [[0.3, 0.1], [0.8, 0.1], [0.7, 0.9], [0.2, 0.8]]
        self.assertEqual(
            WebCameraProcessor._validated_focus(polygon), polygon
        )

    def test_focus_polygon_rejects_tiny_shape(self) -> None:
        with self.assertRaises(ValueError):
            WebCameraProcessor._validated_focus(
                [[0.2, 0.2], [0.22, 0.2], [0.21, 0.23]]
            )

    def test_frigate_restream_uses_go2rtc_sub_stream(self) -> None:
        self.assertEqual(
            frigate_restream_url("http://frigate:5000/api", "gate01"),
            "rtsp://frigate:8554/gate01_sub",
        )


if __name__ == "__main__":
    unittest.main()
