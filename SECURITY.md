# Security policy

## Scope

hAudio 0.01 is intended for a trusted local network. Version 0.01 does not
provide user authentication or HTTPS. The web interface and API listen on
port 8765 and can control audio, recordings, and uploaded sound files.

Do not expose the port directly to the internet or forward it from a router.
Use a firewall, VPN, or an SSH tunnel when access outside the trusted LAN is
needed. Recordings and soundboard files should be treated as private data.

## Reporting a vulnerability

Please report security issues privately to the repository maintainer before
opening a public issue. Include a description, affected version or commit,
reproduction steps, and the potential impact. Do not include passwords,
private keys, network addresses, recordings, or other personal data.

Until a fix is available, avoid publishing exploit details or making affected
instances reachable from untrusted networks.

## Supported versions

Only the latest published version on the default branch is currently
supported. This project is still at version 0.01, so breaking changes may
occur while the architecture is stabilized.
