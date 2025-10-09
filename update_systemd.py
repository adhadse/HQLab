import os
import subprocess
import argparse
import yaml


def mkdir_p(path):
    """Create the directory if it does not exist."""
    if not os.path.exists(path):
        os.makedirs(path)


def get_compose_services(compose_dir):
    """Get list of services defined in a docker-compose.yml file."""
    compose_file = os.path.join(compose_dir, "docker-compose.yml")
    if not os.path.exists(compose_file):
        compose_file = os.path.join(compose_dir, "compose.yml")

    if not os.path.exists(compose_file):
        compose_file = os.path.join(compose_dir, "compose.yaml")

    if not os.path.exists(compose_file):
        return []

    try:
        with open(compose_file, "r") as f:
            compose_data = yaml.safe_load(f)
            if compose_data and "services" in compose_data:
                return list(compose_data["services"].keys())
    except Exception as e:
        print(f"Warning: Could not parse compose file in {compose_dir}: {e}")

    return []


def find_compose_dir_for_container(container_name):
    """Find which compose directory contains the given container and return the project name."""
    base_dir = os.path.expanduser("~/podman_compose")

    # First check if there's a directory with the container name
    direct_path = os.path.join(base_dir, container_name)
    if os.path.exists(direct_path):
        services = get_compose_services(direct_path)
        if container_name in services or not services:
            return (
                container_name,
                direct_path,
                [container_name] if not services else services,
            )

    # If not found, search through all compose directories
    if os.path.exists(base_dir):
        for folder in os.listdir(base_dir):
            folder_path = os.path.join(base_dir, folder)
            if os.path.isdir(folder_path):
                services = get_compose_services(folder_path)
                if container_name in services:
                    # Return the folder name as the project name
                    return folder, folder_path, services

    # Fallback to the original behavior
    return container_name, direct_path, [container_name]


def generate_podlet(container_name):
    """Generate the podlet file for a container and append the necessary Install section."""
    config_dir = os.path.expanduser("~/.config/containers/systemd/")
    os.chdir(config_dir)
    podlet_cmd = (
        f"podlet generate container {container_name} > {container_name}.container"
    )
    subprocess.run(podlet_cmd, shell=True, check=True)

    with open(f"{container_name}.container", "a") as f:
        f.write("\n[Install]\n# Start by default on boot\nWantedBy=default.target\n")


def reload_systemd():
    """Reload the systemd manager configuration."""
    subprocess.run("systemctl --user daemon-reload", shell=True, check=True)


def manage_service(container_name, processed_projects):
    """Check if the service is running, disable it if so, and then manage podman-compose."""
    # Find the correct compose directory for this container
    project_name, podman_compose_dir, all_services = find_compose_dir_for_container(
        container_name
    )

    # Skip if we've already processed this project
    if project_name in processed_projects:
        print(
            f"Skipping {container_name} - already processed as part of {project_name} project"
        )
        return project_name, all_services

    if not os.path.exists(podman_compose_dir):
        print(f"Warning: Compose directory not found for {container_name}, skipping...")
        return None, []

    print(f"Managing {container_name} in compose project: {project_name}")
    print(f"  Services in this project: {', '.join(all_services)}")

    # Stop all services in this compose project
    for service in all_services:
        status_cmd = f"systemctl --user is-active {service}.service"
        is_active = (
            subprocess.run(
                status_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )

        if is_active:
            print(f"  Stopping {service}.service")
            subprocess.run(
                f"systemctl --user stop {service}.service", shell=True, check=True
            )

    # Check if any containers from this project are still running
    any_container_running = False
    for service in all_services:
        podman_ps_cmd = f"podman ps --format '{{{{.Names}}}}' | grep -w {service}"
        if (
            subprocess.run(
                podman_ps_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ):
            any_container_running = True
            break

    # Change directory to the compose directory
    os.chdir(podman_compose_dir)

    if any_container_running:
        print(f"  Running: podman compose down")
        subprocess.run("podman compose down", shell=True, check=True)

    print(f"  Running: podman compose up -d")
    subprocess.run("podman compose up -d", shell=True, check=True)

    # Generate podlet files for all services in this project
    print(f"  Generating systemd service files...")
    for service in all_services:
        generate_podlet(service)

    os.chdir(os.path.expanduser(f"~/podman_compose/"))

    return project_name, all_services


def start_service(container_name):
    """Start service using systemd."""
    print(f"Starting {container_name}.service")
    result = subprocess.run(
        f"systemctl --user start {container_name}.service",
        shell=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Warning: Could not start {container_name}.service: {result.stderr}")
    else:
        print(f"  Successfully started {container_name}.service")


def enable_linger():
    """Enable lingering for the current user."""
    user = os.getenv("USER")
    subprocess.run(f"loginctl enable-linger {user}", shell=True, check=True)


def get_running_containers():
    """Get a list of currently running container names."""
    result = subprocess.run(
        "podman ps --format '{{.Names}}'", shell=True, capture_output=True, text=True
    )
    containers = result.stdout.strip().split("\n")
    return [c for c in containers if c]  # Filter out empty strings


def main():
    parser = argparse.ArgumentParser(
        description="Generate systemd service files for podman containers"
    )
    parser.add_argument(
        "containers", nargs="*", help="Names of the containers to manage"
    )
    parser.add_argument(
        "--all", action="store_true", help="Manage all running containers"
    )
    args = parser.parse_args()

    # Create the necessary directory
    config_dir = os.path.expanduser("~/.config/containers/systemd/")
    mkdir_p(config_dir)

    # Determine which containers to manage
    if args.all:
        container_names = get_running_containers()
    else:
        container_names = args.containers

    if not container_names:
        print("No containers specified or found to manage.")
        return

    # Track which compose projects we've already processed
    processed_projects = set()
    services_to_start = []

    # Manage services for each container
    for container_name in container_names:
        project_name, services = manage_service(container_name, processed_projects)
        if project_name:
            processed_projects.add(project_name)
            services_to_start.extend(services)

    print("\nReloading systemd daemon...")
    reload_systemd()

    # Start all services (removing duplicates)
    services_to_start = list(set(services_to_start))
    print(f"\nStarting services: {', '.join(services_to_start)}")
    for service_name in services_to_start:
        start_service(service_name)

    print("\nEnabling linger...")
    enable_linger()

    print("\n✓ All operations completed successfully!")


if __name__ == "__main__":
    main()
