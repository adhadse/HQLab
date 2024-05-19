import os
import subprocess
import argparse

def mkdir_p(path):
    """Create the directory if it does not exist."""
    if not os.path.exists(path):
        os.makedirs(path)

def generate_podlet(container_name):
    """Generate the podlet file for a container and append the necessary Install section."""
    config_dir = os.path.expanduser("~/.config/containers/systemd/")
    os.chdir(config_dir)
    podlet_cmd = f"podlet generate container {container_name} > {container_name}.container"
    subprocess.run(podlet_cmd, shell=True, check=True)
    
    with open(f"{container_name}.container", 'a') as f:
        f.write('\n[Install]\n# Start by default on boot\nWantedBy=default.target\n')

def reload_systemd():
    """Reload the systemd manager configuration."""
    subprocess.run("systemctl --user daemon-reload", shell=True, check=True)

def manage_service(container_name):
    """Check if the service is running, disable it if so, and then manage podman-compose."""
    status_cmd = f"systemctl --user is-active {container_name}.service"
    is_active = subprocess.run(status_cmd, shell=True).returncode == 0
    
    if is_active:
        # subprocess.run(f"systemctl --user disable {container_name}.service", shell=True, check=True)
        subprocess.run(f"systemctl --user stop {container_name}.service", shell=True, check=True)

    # Check if container is still running
    podman_ps_cmd = f"podman ps --format '{{{{.Names}}}}' | grep -w {container_name}"
    is_container_running = subprocess.run(podman_ps_cmd, shell=True).returncode == 0

    podman_compose_dir = os.path.expanduser(f"~/podman_compose/{container_name}")
    # Change directory to ~/podman_compose/<container_name>
    os.chdir(podman_compose_dir)
    if is_container_running or not is_active:
        if os.path.exists(podman_compose_dir):
            subprocess.run("podman compose down", shell=True, check=True)
    subprocess.run("podman compose up -d", shell=True, check=True)
    
    # Generate the podlet file after managing podman-compose
    generate_podlet(container_name)
    os.chdir(os.path.expanduser(f"~/podman_compose/"))

def start_service(container_name):
    """Enable service using systemd."""
    subprocess.run(f"systemctl --user start {container_name}.service", shell=True, check=True)

def enable_linger():
    """Enable lingering for the current user."""
    user = os.getenv("USER")
    subprocess.run(f"loginctl enable-linger {user}", shell=True, check=True)

def get_running_containers():
    """Get a list of currently running container names."""
    result = subprocess.run("podman ps --format '{{.Names}}'", shell=True, capture_output=True, text=True)
    return result.stdout.strip().split('\n')

def main():
    parser = argparse.ArgumentParser(description="Generate systemd service files for podman containers")
    parser.add_argument("containers", nargs="*", help="Names of the containers to manage")
    parser.add_argument("--all", action="store_true", help="Manage all running containers")
    args = parser.parse_args()

    # Create the necessary directory
    config_dir = os.path.expanduser("~/.config/containers/systemd/")
    mkdir_p(config_dir)
    
    # # Change to the directory
    # os.chdir(config_dir)

    # Determine which containers to manage
    if args.all:
        container_names = get_running_containers()
    else:
        container_names = args.containers
    
    if not container_names:
        print("No containers specified or found to manage.")
        return

    # Manage services for each container
    for container_name in container_names:
        manage_service(container_name)
    
    reload_systemd()

    for container_name in container_names:
        start_service(container_name)


    enable_linger()

if __name__ == "__main__":
    main()
