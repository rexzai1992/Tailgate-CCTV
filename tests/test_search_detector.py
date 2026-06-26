import unittest

import numpy as np

from src.search_detector import (
    estimate_shirt_color,
    object_inside_person,
    parse_search_query,
)


MODEL_NAMES = {
    0: "person",
    24: "backpack",
    26: "handbag",
    28: "suitcase",
    39: "bottle",
    63: "laptop",
    67: "cell phone",
}


class SearchDetectorTests(unittest.TestCase):
    def test_phone_alias_maps_to_cell_phone(self) -> None:
        spec = parse_search_query("phone", MODEL_NAMES)
        self.assertTrue(spec.supported)
        self.assertEqual(spec.mode, "object")
        self.assertEqual(spec.class_ids, (67,))

    def test_holding_phone_requests_person_association(self) -> None:
        spec = parse_search_query("person holding phone", MODEL_NAMES)
        self.assertEqual(spec.mode, "person_with_object")
        self.assertEqual(spec.target, "cell phone")

    def test_red_shirt_uses_color_search(self) -> None:
        spec = parse_search_query("find red shirt", MODEL_NAMES)
        self.assertEqual(spec.mode, "shirt_color")
        self.assertEqual(spec.color, "red")
        self.assertEqual(spec.class_ids, (0,))

    def test_airpods_reports_custom_model_requirement(self) -> None:
        spec = parse_search_query("airpods", MODEL_NAMES)
        self.assertFalse(spec.supported)
        self.assertIn("custom model", spec.message.lower())

    def test_bag_search_includes_common_bag_classes(self) -> None:
        spec = parse_search_query("bag", MODEL_NAMES)
        self.assertEqual(spec.class_ids, (24, 26, 28))

    def test_estimates_red_torso(self) -> None:
        frame = np.zeros((200, 100, 3), dtype=np.uint8)
        frame[36:116, 18:82] = (0, 0, 255)
        color, score = estimate_shirt_color(frame, (0, 0, 100, 200))
        self.assertEqual(color, "red")
        self.assertGreater(score, 0.8)

    def test_object_center_can_be_associated_with_person(self) -> None:
        self.assertTrue(object_inside_person((70, 80, 90, 110), (20, 20, 120, 190)))
        self.assertFalse(object_inside_person((180, 80, 200, 110), (20, 20, 120, 190)))


if __name__ == "__main__":
    unittest.main()
