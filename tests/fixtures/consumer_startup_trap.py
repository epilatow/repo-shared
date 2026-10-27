"""Consumer startup hook that fails if pytest loads it."""

raise RuntimeError("consumer startup needs local npm tools")
