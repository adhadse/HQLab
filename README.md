# HQLab
Contains Scripts and Podman compose files to run service on my private server.

This folder sits inside `~/podman_compose` folder on my server. From here, you can can either go one by one into `compose.yaml` file and do the setup according to each application.

## Currently used services
1. Traefik: as reverse proxy and TLS certificates handler
2. PiHole: for local DNS resolver
3. Portainer: UI for managing containers started with podman.
4. Uptime-Kuma: status page service.
5. Paisa: a local first personal finance management service.

## Updating the services
The podman containers by default ignore the Docker compose `restart: ` key. To mitigate that podman team suggest to rely on systemd. We can create systemd `.container` file and enable it as a service. 

The `update_systemd.py` is useful for just that. It relies on `podlet` binary to create a `.container` file from a running container. Which can then be enabled as a systemd service.

- Run `python3 update_systemd.py <container_name>` to create and enable a specific container as a service.
- Run `python3 update_systemd.py --all` to create/update all currently running podman containers as a service.