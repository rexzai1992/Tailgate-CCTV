# Gym Sentry doorway field test

Use this checklist after mounting the real entrance camera. Do not accept the
setup until the first row passes.

## Setup

1. Open <http://127.0.0.1:8080/>.
2. Select the intended entrance camera. If it is missing, reconnect it and use
   **Refresh cameras**. Gym Sentry will not silently fall back to another
   camera.
3. Draw the doorway focus area so only the usable entrance is visible.
4. Draw the counting line across the walking path.
5. Confirm the green IN arrow points in the real entry direction.
6. Leave **Diagnostics: on** while testing.
7. Reset counts and tracking before each scenario.

## Acceptance scenarios

| Scenario | Trials | Expected result |
|---|---:|---|
| One person enters normally | 10 | IN increases exactly 10; zero tailgating alerts |
| Two different people follow within 4 seconds | 10 | Second person creates an alert, image, body crop, and video |
| One person stops on the line | 5 | No count until the person fully crosses |
| One person approaches then turns around | 5 | No IN count |
| One person moves back and forth near the line | 5 | At most one IN count; never a two-person alert |
| Two people enter side by side | 5 | Two distinct tracker IDs; second person alerts |
| Person enters with face partly hidden | 5 | Body and event image save; face crop is optional |
| Dim entrance lighting | 5 | Stable tracker ID through the crossing |

## What to watch

- A person should keep one tracker ID while crossing.
- The overlay should move from **OUT SIDE** to **IN SIDE**.
- **ON LINE** means the anchor is inside the line deadband and is not counted.
- A repeated IN from the same tracker is blocked for 8 seconds and must first
  move far enough back to re-arm.
- Tailgating requires different tracker IDs inside the four-second window.

If the 10 single-entry test fails, adjust the focus area and line before
changing timing values. A diagonal line near the bottom edge often causes foot
box jitter; place the line where the full body remains visible on both sides.
