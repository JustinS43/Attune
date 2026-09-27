# Caption placement and sound direction

## Goal

Keep captions readable in a fixed place when speech comes from outside the camera view. Show the reported direction with an arrow. Keep a visible person's caption attached to their face.

## Work plan

1. Use the existing fused speaker and direction in the lens view. Route speech with no visible face to one bottom caption area; preserve its speaker label and text.
2. Draw a left, right, or behind arrow in that caption. If direction is unknown, show no arrow. Keep the caption's position fixed when the reported side changes.
3. Leave face-attached bubbles in place for speakers visible in the frame. When a face leaves the frame, move its continuing caption to the bottom after the existing short tracking hold.
4. Review left, right, behind, unknown, and visible-face cases before marking the PR ready. The compact glasses modes already use fixed caption areas with direction indicators.

This changes caption placement only. Sound classification, direction estimation, and speaker attribution continue to come from the engine.
