from pathlib import Path
import unittest

import yaml


class FrigateAssetsTests(unittest.TestCase):
    def test_frigate_example_has_record_and_restream_roles(self) -> None:
        config = yaml.safe_load(
            Path("frigate/config.yml.example").read_text(
                encoding="utf-8"
            )
        )

        self.assertTrue(config["mqtt"]["enabled"])
        self.assertIn("gate01_main", config["go2rtc"]["streams"])
        self.assertIn("gate01_sub", config["go2rtc"]["streams"])
        self.assertEqual(
            config["ffmpeg"]["hwaccel_args"], "preset-nvidia"
        )
        self.assertFalse(config["detect"]["enabled"])
        self.assertEqual(config["record"]["continuous"]["days"], 14)
        inputs = config["cameras"]["gate01"]["ffmpeg"]["inputs"]
        self.assertIn("record", inputs[0]["roles"])
        self.assertIn("detect", inputs[1]["roles"])

    def test_compose_exposes_required_ports(self) -> None:
        compose = yaml.safe_load(
            Path("docker-compose.frigate.yml").read_text(
                encoding="utf-8"
            )
        )
        ports = compose["services"]["frigate"]["ports"]

        self.assertIn("8971:8971", ports)
        self.assertIn("${FRIGATE_INTERNAL_PORT:-5000}:5000", ports)
        self.assertIn("8554:8554", ports)
        self.assertIn("8555:8555/tcp", ports)
        self.assertIn("8555:8555/udp", ports)


if __name__ == "__main__":
    unittest.main()
