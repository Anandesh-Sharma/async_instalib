# Rules for async_instalib

Read the root README, package README, and relevant source before editing.

## README style

Keep the root README a short showcase: the benefit, a few features, installation,
and one complete quick-start example. Link to examples and the
[package README](src/async_instalib/README.md) for API details, limitations, verification,
architecture, contribution instructions and release steps. Do not expand the
front page into API documentation. Update the package README when behavior changes.

## After pushing

After every push, wait for all triggered CI/CD runs for that exact commit to pass.
Fix failures and recheck before declaring completion; report any external blocker.
