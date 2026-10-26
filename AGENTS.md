# Android builder maintenance

- This repository builds the Mullvad Android app and compatible forks. Keep the
  upstream digest in base-image.txt in sync with the selected source commit.
- On this Windows workstation use scripts/Build.ps1, which invokes the existing
  Docker Engine in WSL Ubuntu. Do not install another toolchain or require Docker
  Desktop without inspecting the existing environment.
- Use persistent volumes for Gradle, Cargo registry, Cargo Git, Cargo targets and
  the Linux source checkout. Never put build I/O on the read-only source bind.
- Never add keystores, passwords, APKs, local tokens or DPAPI blobs to Git or the
  Docker build context. Signing stays outside the build container.
- A release must have a resolved source commit, non-debuggable package metadata,
  validated native ELF architectures, zip alignment and SHA-256 provenance.
- Do not discard tracked edits or automatically uninstall an existing Android app.
- Run Bash syntax checks, Python unit tests and PowerShell parser checks after
  changing scripts. Verify container changes with a real release APK build.
- New Git branch names must never contain codex/.
