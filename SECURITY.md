# Security policy

## Scope

hAudio 0.01 is intended for a trusted local network. Version 0.01 does not
provide user authentication or HTTPS. The web interface and API listen on
port 8765 and can control audio, recordings, and uploaded sound files.

Do not expose the port directly to the internet or forward it from a router.
Use a firewall, VPN, or an SSH tunnel when access outside the trusted LAN is
needed. Recordings and soundboard files should be treated as private data.

Browser WebSocket connections are restricted to the same host as the web
interface, uploaded files are size-limited and validated as MP3 audio, and
filenames are confined to their configured storage directories. These checks
do not replace authentication for untrusted networks.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting form at
<https://github.com/GiT-BeDa/hAudio/security/advisories/new>. Private reporting
must be enabled in the repository settings when the project becomes public. If
the form is unavailable, request a private contact channel through
<https://www.bk99.de> without posting vulnerability details publicly.

Include a description, affected version or commit, reproduction steps, and the
potential impact. Do not include passwords, private keys, network addresses,
recordings, or other personal data.

Until a fix is available, avoid publishing exploit details or making affected
instances reachable from untrusted networks.

## Supported versions

Only the latest published version on the default branch is currently
supported. This project is still at version 0.01, so breaking changes may
occur while the architecture is stabilized.
