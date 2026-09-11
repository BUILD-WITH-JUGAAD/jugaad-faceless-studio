# Contributing to JUGAAD Faceless Studio

Thanks for your interest in improving JUGAAD Faceless Studio.

The project is intentionally open to experimentation and improvement.

## Development workflow

Do not push directly to `main`.

1. Fork the repository or create a feature branch.
2. Make your changes.
3. Add or update tests where appropriate.
4. Run the existing test suite.
5. Push your branch.
6. Open a Pull Request.
7. Explain what changed, why, and how it was tested.

## Important

Please avoid large rewrites when a focused change is sufficient.

Existing functionality should remain backwards compatible unless the PR explicitly proposes a breaking change.

In particular, consider:

- existing PARTS format
- CLI behavior
- Studio behavior
- visual planner
- legacy pipeline
- stock providers
- AI generation providers
- reference images
- character consistency
- caching/reuse
- captions
- TTS
- music
- SFX
- MoviePy/FFmpeg assembly

## Experiments are welcome

Ideas for improving:

- semantic relevance
- visual diversity
- scene planning
- asset retrieval
- AI generation
- character consistency
- transitions
- pacing
- caching
- performance
- cost efficiency
- prompt engineering
- evaluation/QA

are welcome.

For larger architectural changes, open an issue first so the approach can be discussed before implementation.
