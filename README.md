<p align="center">
  <img src="https://raw.githubusercontent.com/Anandesh-Sharma/async_instalib/main/assets/banner.png" alt="async_instalib — Async Python for Instagram" width="100%">
</p>

# async_instalib

**Instagram, from Python. Async by default.**

[![PyPI](https://img.shields.io/pypi/v/async_instalib?color=111111)](https://pypi.org/project/async_instalib/)
[![CI](https://github.com/Anandesh-Sharma/async_instalib/actions/workflows/ci.yml/badge.svg)](https://github.com/Anandesh-Sharma/async_instalib/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-111111)](https://github.com/Anandesh-Sharma/async_instalib/blob/main/LICENSE)

- Find people and explore profiles.
- Publish posts, reels and stories.
- Manage comments, DMs and account events.

## Get started

Python 3.11+ · macOS or Linux.

```sh
pip install async_instalib
```

Sign in once. Your session is saved for the next run.

```sh
async-instalib login --session ~/.async_instalib.session.json
```

Look up a profile:

```python
import asyncio
from async_instalib import Instagram

async def main():
    async with Instagram.from_session_file("~/.async_instalib.session.json") as instagram:
        profile = await instagram.people.by_username("instagram")
        print(profile.username, profile.follower_count)

asyncio.run(main())
```

[More examples](https://github.com/Anandesh-Sharma/async_instalib/tree/main/examples) ·
[Usage & reference](https://github.com/Anandesh-Sharma/async_instalib/blob/main/src/async_instalib/README.md) ·
[Report an issue](https://github.com/Anandesh-Sharma/async_instalib/issues)

<sub>Unofficial client. Not affiliated with Instagram. Private APIs can change.</sub>
