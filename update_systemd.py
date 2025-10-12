#!/usr/bin/env python3
# update_systemd.py

import os
import subprocess
import argparse
import yaml
import json
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ============================================================================
# GCP Configuration
# ============================================================================
GCP_PROJECT_ID = "homelab-462205"
GCP_SERVICE_ACCOUNT_KEY = "/home/adhadse/.config/.gcp/homelab-462205-8d906c79fe59.json"
COMPOSE_BASE_DIR = os.path.expanduser("~/podman_compose")

# ============================================================================
# Helper Functions
# ============================================================================


def mkdir_p(path):
    """Create the directory if it does not exist."""
    if not os.path.exists(path):
        os.makedirs(path)


def activate_gcp_service_account(key_file: str, project_id: str):
    """Activate GCP service account and set project."""
    if not os.path.exists(key_file):
        raise FileNotFoundError(f"GCP service account key not found: {key_file}")

    print(f"🔑 Activating GCP service account...")

    # Activate service account
    subprocess.run(
        ["gcloud", "auth", "activate-service-account", "--key-file", key_file],
        check=True,
        capture_output=True,
    )

    # Set project
    subprocess.run(
        ["gcloud", "config", "set", "project", project_id],
        check=True,
        capture_output=True,
    )

    print(f"✅ Service account activated for project: {project_id}\n")


def get_compose_data(compose_dir: str) -> Tuple[Optional[dict], Optional[str]]:
    """Load and return the compose file data and path."""
    for filename in ["docker-compose.yml", "compose.yml", "compose.yaml"]:
        compose_file = os.path.join(compose_dir, filename)
        if os.path.exists(compose_file):
            try:
                with open(compose_file, "r") as f:
                    return yaml.safe_load(f), compose_file
            except Exception as e:
                print(f"Warning: Could not parse {compose_file}: {e}")
    return None, None


def get_x_config(compose_data: dict) -> dict:
    """Extract x-config from compose file with defaults."""
    default_config = {
        "enabled": False,
        "enable_gcp_integration": False,
        "secret_name": None,
        "config_name": None,
    }

    if not compose_data:
        return default_config

    x_config = compose_data.get("x-config", {})

    # Merge with defaults
    return {**default_config, **x_config}


def discover_compose_projects(base_dir: str) -> Dict[str, dict]:
    """
    Discover all compose projects in base directory.
    Returns dict: {project_name: {path, config, services}}
    """
    projects = {}

    if not os.path.exists(base_dir):
        print(f"Warning: Base directory does not exist: {base_dir}")
        return projects

    for folder in os.listdir(base_dir):
        folder_path = os.path.join(base_dir, folder)
        if not os.path.isdir(folder_path):
            continue

        compose_data, compose_file = get_compose_data(folder_path)
        if not compose_data:
            continue

        x_config = get_x_config(compose_data)
        services = list(compose_data.get("services", {}).keys())

        projects[folder] = {
            "path": folder_path,
            "compose_file": compose_file,
            "config": x_config,
            "services": services,
            "compose_data": compose_data,
        }

    return projects


def list_projects(base_dir: str):
    """List all discovered projects with their status."""
    projects = discover_compose_projects(base_dir)

    if not projects:
        print("No compose projects found.")
        return

    print("\n" + "=" * 80)
    print("DISCOVERED COMPOSE PROJECTS")
    print("=" * 80)

    enabled_projects = []
    disabled_projects = []

    for project_name, info in sorted(projects.items()):
        config = info["config"]
        services = info["services"]

        status = "✅ ENABLED" if config["enabled"] else "⏸️  DISABLED"
        gcp_status = "🔐 GCP" if config["enable_gcp_integration"] else "📝 Local"

        project_info = {
            "name": project_name,
            "status": status,
            "gcp": gcp_status,
            "services": services,
            "config": config,
        }

        if config["enabled"]:
            enabled_projects.append(project_info)
        else:
            disabled_projects.append(project_info)

    # Print enabled projects
    if enabled_projects:
        print("\n✅ ENABLED PROJECTS:")
        print("-" * 80)
        for p in enabled_projects:
            print(f"\n  Project: {p['name']}")
            print(f"    Status: {p['status']} | {p['gcp']}")
            print(f"    Services: {', '.join(p['services'])}")
            if p["config"]["enable_gcp_integration"]:
                secret_name = p["config"]["secret_name"] or f"{p['name']}-secrets"
                config_name = p["config"]["config_name"] or f"{p['name']}-config"
                print(f"    Secret: {secret_name}")
                print(f"    Config: {config_name}")

    # Print disabled projects
    if disabled_projects:
        print("\n\n⏸️  DISABLED PROJECTS:")
        print("-" * 80)
        for p in disabled_projects:
            print(f"\n  Project: {p['name']}")
            print(f"    Services: {', '.join(p['services'])}")

    print("\n" + "=" * 80)
    print(f"Total: {len(enabled_projects)} enabled, {len(disabled_projects)} disabled")
    print("=" * 80 + "\n")


def fetch_secrets_to_tmpfs(
    project_name: str,
    secret_name: str,
    gcp_project_id: str,
    dry_run: bool = False,
    show_secrets: bool = False,
) -> Tuple[str, dict]:
    """Fetch secrets from GCP and store in tmpfs (RAM only)."""
    secrets_dir = f"/dev/shm/podman-secrets-{project_name}"

    print(f"  🔐 Fetching secrets: {secret_name}")

    if dry_run and not show_secrets:
        print(f"  [DRY RUN] Would fetch secrets from GCP")
        return secrets_dir, {}

    secrets_json = {}

    try:
        secrets_result = subprocess.run(
            [
                "gcloud",
                "secrets",
                "versions",
                "access",
                "latest",
                "--secret",
                secret_name,
                "--project",
                gcp_project_id,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        secrets_json = json.loads(secrets_result.stdout)

        if show_secrets:
            print(f"  📋 Secrets fetched:")
            for key, value in secrets_json.items():
                # Mask sensitive values
                masked_value = value[:4] + "..." if len(str(value)) > 4 else "***"
                print(f"     {key}: {masked_value}")

        if dry_run:
            return secrets_dir, secrets_json

        # Clean up old secrets if they exist
        if os.path.exists(secrets_dir):
            shutil.rmtree(secrets_dir)

        os.makedirs(secrets_dir, mode=0o700)

        # Write each secret to tmpfs
        for key, value in secrets_json.items():
            secret_file = os.path.join(secrets_dir, key)
            with open(secret_file, "w") as f:
                f.write(str(value))
            os.chmod(secret_file, 0o600)

        print(f"  ✅ {len(secrets_json)} secrets stored in tmpfs (RAM only)")

    except subprocess.CalledProcessError as e:
        print(f"  ⚠️  No secrets found: {secret_name}")
        if e.stderr:
            print(f"     Error: {e.stderr.strip()}")

    return secrets_dir, secrets_json


def fetch_parameters(
    config_name: str,
    gcp_project_id: str,
    dry_run: bool = False,
    show_secrets: bool = False,
) -> dict:
    """Fetch parameters from GCP Runtime Config."""
    print(f"  📦 Fetching parameters: {config_name}")

    if dry_run and not show_secrets:
        print(f"  [DRY RUN] Would fetch parameters from GCP")
        return {}

    params = {}

    try:
        params_result = subprocess.run(
            [
                "gcloud",
                "beta",
                "runtime-config",
                "configs",
                "variables",
                "list",
                "--config-name",
                config_name,
                "--format",
                "json",
                "--project",
                gcp_project_id,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        params_list = json.loads(params_result.stdout)
        for item in params_list:
            key = os.path.basename(item["name"])
            value = item.get("value", item.get("text", ""))
            params[key] = value

        print(f"  ✅ {len(params)} parameters fetched")

        if show_secrets:
            print(f"  📋 Parameters fetched:")
            for key, value in params.items():
                print(f"     {key}: {value}")

    except subprocess.CalledProcessError as e:
        print(f"  ⚠️  No parameters found: {config_name}")
        if e.stderr:
            print(f"     Error: {e.stderr.strip()}")

    return params


def update_compose_file_with_secrets(
    compose_file: str,
    compose_data: dict,
    secrets_dir: str,
    secrets_json: dict,
    params: dict,
    dry_run: bool = False,
    show_secrets: bool = False,
) -> str:
    """Update compose file to use tmpfs secrets and parameters.

    Priority: Secrets > Parameters
    - If a key exists in secrets_json, use that value
    - Otherwise, if it exists in params, use that value
    - Overwrites existing environment variables
    """

    if dry_run and not show_secrets:
        print(f"  [DRY RUN] Would update compose file with secrets and parameters")
        return compose_file

    # Make a deep copy to avoid modifying original
    import copy

    compose_data = copy.deepcopy(compose_data)

    # Merge secrets and params (secrets take priority)
    combined_env_vars = {}

    # First add parameters
    if params:
        combined_env_vars.update(params)

    # Then add/overwrite with secrets (higher priority)
    if secrets_json:
        combined_env_vars.update(secrets_json)

    if show_secrets:
        print(f"  📋 Combined environment variables to inject:")
        for key, value in combined_env_vars.items():
            source = "secret" if key in secrets_json else "param"
            masked_value = str(value)[:8] + "..." if len(str(value)) > 8 else "***"
            print(f"     {key}: {masked_value} (from {source})")

    # Update secrets section to point to tmpfs (only for actual file-based secrets)
    if secrets_json and "secrets" in compose_data:
        for secret_name in compose_data["secrets"]:
            if secret_name in secrets_json:
                compose_data["secrets"][secret_name] = {
                    "file": f"{secrets_dir}/{secret_name}"
                }

    # Inject/overwrite combined variables as environment variables in services
    if combined_env_vars and "services" in compose_data:
        for service_name, service_config in compose_data["services"].items():
            if "environment" not in service_config:
                service_config["environment"] = {}

            env = service_config["environment"]

            if isinstance(env, dict):
                # OVERWRITE existing keys with combined values
                for key, value in combined_env_vars.items():
                    env[key] = str(value)

            elif isinstance(env, list):
                # Convert list to dict, update, then convert back
                env_dict = {}
                for item in env:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        env_dict[k] = v
                    else:
                        env_dict[item] = ""

                # Overwrite with combined values
                for key, value in combined_env_vars.items():
                    env_dict[key] = str(value)

                # Convert back to list
                service_config["environment"] = [
                    f"{k}={v}" if v else k for k, v in env_dict.items()
                ]

    # Write updated compose file to temp location
    temp_compose = compose_file + ".tmp"
    with open(temp_compose, "w") as f:
        yaml.dump(compose_data, f, default_flow_style=False, sort_keys=False)

    if show_secrets:
        print(f"\n  📄 Generated compose file (relevant sections):")
        print("  " + "-" * 76)
        with open(temp_compose, "r") as f:
            content = f.read()
            # Show only services section for brevity
            if "services:" in content:
                in_services = False
                for line in content.split("\n"):
                    if line.startswith("services:"):
                        in_services = True
                    elif in_services and line and not line.startswith(" "):
                        break
                    if in_services:
                        print(f"  {line}")
        print("  " + "-" * 76 + "\n")

    return temp_compose


def get_container_name_for_service(service_name: str, compose_data: dict) -> str:
    """Get the actual container name for a service (handles container_name override)."""
    service_config = compose_data.get("services", {}).get(service_name, {})
    container_name = service_config.get("container_name", service_name)
    return container_name


def generate_podlet(container_name: str, service_name: str):
    """Generate the podlet file for a container.

    Args:
        container_name: The actual container name (e.g., 'postgres', 'pgadmin')
        service_name: The service name from compose file (e.g., 'db', 'pgadmin')
    """
    config_dir = os.path.expanduser("~/.config/containers/systemd/")
    os.chdir(config_dir)

    # Use service_name for the .container file
    service_file = f"{service_name}.container"

    podlet_cmd = f"podlet generate container {container_name} > {service_file}"
    subprocess.run(podlet_cmd, shell=True, check=True)

    with open(service_file, "a") as f:
        f.write("\n[Unit]\n")
        f.write("After=podman-secrets-loader.service\n")  # ADD THIS
        f.write("Requires=podman-secrets-loader.service\n")  # ADD THIS
        f.write("\n[Install]\n")
        f.write("WantedBy=default.target\n")


def reload_systemd():
    """Reload the systemd manager configuration."""
    subprocess.run("systemctl --user daemon-reload", shell=True, check=True)


def manage_project(
    project_name: str,
    project_info: dict,
    gcp_project_id: str,
    dry_run: bool = False,
    show_secrets: bool = False,
) -> List[str]:
    """Manage a single compose project."""

    config = project_info["config"]
    services = project_info["services"]
    compose_dir = project_info["path"]
    compose_file = project_info["compose_file"]
    compose_data = project_info["compose_data"]

    print(f"\n{'=' * 80}")
    print(f"Managing project: {project_name}")
    print(f"  Directory: {compose_dir}")
    print(f"  Services: {', '.join(services)}")
    print(
        f"  GCP Integration: {'Enabled' if config['enable_gcp_integration'] else 'Disabled'}"
    )
    print(f"{'=' * 80}")

    if dry_run:
        print("  [DRY RUN] Would start services")
        return services

    secrets_dir = None
    secrets_json = {}
    params = {}

    # Fetch secrets and parameters from GCP if enabled
    if config["enable_gcp_integration"]:
        secret_name = config["secret_name"] or None
        config_name = config["config_name"] or None

        secrets_dir, secrets_json = fetch_secrets_to_tmpfs(
            project_name, secret_name, gcp_project_id, dry_run, show_secrets
        )
        if config_name:
            params = fetch_parameters(
                config_name, gcp_project_id, dry_run, show_secrets
            )

    # Update compose file with secrets path and parameters
    compose_file_to_use = compose_file
    if secrets_json or params:
        temp_compose = update_compose_file_with_secrets(
            compose_file,
            compose_data,
            secrets_dir,
            secrets_json,
            params,
            dry_run,
            show_secrets,
        )
        compose_file_to_use = temp_compose

    # Stop running services
    print(f"  Stopping existing services...")
    for service_name in services:
        status_cmd = f"systemctl --user is-active {service_name}.service"
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
            print(f"    Stopping {service_name}.service")
            subprocess.run(
                f"systemctl --user stop {service_name}.service",
                shell=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    # Check if any containers are running
    any_running = False
    for service_name in services:
        container_name = get_container_name_for_service(service_name, compose_data)
        if (
            subprocess.run(
                f"podman ps --format '{{{{.Names}}}}' | grep -w {container_name}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ):
            any_running = True
            break

    os.chdir(compose_dir)

    if any_running:
        print(f"  Running: podman compose down")
        subprocess.run(
            "podman compose down",
            shell=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Use updated compose file if we have secrets/params
    compose_cmd = "podman compose"
    if compose_file_to_use != compose_file:
        compose_cmd += f" -f {compose_file_to_use}"

    print(f"  Running: {compose_cmd} up -d --force-recreate")
    result = subprocess.run(
        f"{compose_cmd} up -d --force-recreate",
        shell=True,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ❌ Error starting services:")
        print(f"     {result.stderr}")
        return []

    print(f"  ✅ Services started successfully")

    # Clean up temp compose file
    if compose_file_to_use != compose_file:
        try:
            os.remove(compose_file_to_use)
        except:
            pass

    # Generate systemd service files for each service
    print(f"  Generating systemd service files...")
    config_dir = os.path.expanduser("~/.config/containers/systemd/")

    for service_name in services:
        try:
            # Get the actual container name (may differ from service name)
            container_name = get_container_name_for_service(service_name, compose_data)

            print(
                f"    Generating {service_name}.container (container: {container_name})"
            )
            generate_podlet(container_name, service_name)
            print(f"    ✅ Generated {service_name}.container")
        except Exception as e:
            print(f"    ⚠️  Could not generate {service_name}.container: {e}")

    return services


def start_service(service_name: str):
    """Start service using systemd."""
    print(f"  Starting {service_name}.service")
    result = subprocess.run(
        f"systemctl --user start {service_name}.service",
        shell=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"    ⚠️  Could not start: {result.stderr.strip()}")
    else:
        print(f"    ✅ Started successfully")


def enable_linger():
    """Enable lingering for the current user."""
    user = os.getenv("USER")
    subprocess.run(
        f"loginctl enable-linger {user}",
        shell=True,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cleanup_old_secrets():
    """Clean up old secret directories from tmpfs."""
    tmpfs_dir = "/dev/shm"
    cleaned = 0

    for item in os.listdir(tmpfs_dir):
        if item.startswith("podman-secrets-"):
            secret_path = os.path.join(tmpfs_dir, item)
            try:
                shutil.rmtree(secret_path)
                cleaned += 1
            except Exception as e:
                print(f"  Warning: Could not clean up {item}: {e}")

    if cleaned > 0:
        print(
            f"  Cleaned up {cleaned} old secret director{'y' if cleaned == 1 else 'ies'}"
        )


def fetch_all_secrets(base_dir: str, gcp_project_id: str, service_account_key: str):
    """Fetch all secrets for enabled projects (used on boot)."""
    activate_gcp_service_account(service_account_key, gcp_project_id)

    projects = discover_compose_projects(base_dir)
    enabled_projects = {
        name: info
        for name, info in projects.items()
        if info["config"]["enabled"] and info["config"]["enable_gcp_integration"]
    }

    for project_name, project_info in enabled_projects.items():
        config = project_info["config"]
        secret_name = config["secret_name"]

        if secret_name:
            fetch_secrets_to_tmpfs(project_name, secret_name, gcp_project_id)


def ensure_podman_secrets_service():
    """Check if systemd service file exists, create it if not."""

    service_path = Path.home() / ".config/systemd/user/podman-secrets-loader.service"

    if service_path.exists():
        print(f"Service file already exists at: {service_path}")
        return

    # Create directory if it doesn't exist
    service_path.parent.mkdir(parents=True, exist_ok=True)

    # Service file content
    service_content = """[Unit]
Description=Load GCP secrets for Podman containers
Before=default.target
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/home/adhadse/podman_compose/update_systemd.py --fetch-secrets-only
RemainAfterExit=yes

[Install]
WantedBy=default.target
"""

    # Write the service file
    service_path.write_text(service_content)
    print(
        f"Load GCP secrets for Podman containers Service file created at: {service_path}"
    )


# ============================================================================
# Main Function
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Manage Podman Compose projects with GCP secret integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all available projects
  %(prog)s --list

  # Start specific projects
  %(prog)s kener postgres

  # Start all enabled projects
  %(prog)s --all

  # Use different GCP project
  %(prog)s --all --project-id my-other-project

  # Use different base directory
  %(prog)s --all --base-dir ~/my-compose-files

  # Dry run (show what would happen)
  %(prog)s --all --dry-run

  # Clean up old secrets
  %(prog)s --cleanup
        """,
    )

    parser.add_argument(
        "projects", nargs="*", help="Names of the projects to manage (folder names)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Manage all enabled projects (x-config.enabled=true)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all discovered projects and their configuration",
    )
    parser.add_argument(
        "--project-id",
        default=GCP_PROJECT_ID,
        help=f"GCP Project ID (default: {GCP_PROJECT_ID})",
    )
    parser.add_argument(
        "--base-dir",
        default=COMPOSE_BASE_DIR,
        help=f"Base directory for compose files (default: {COMPOSE_BASE_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without doing it",
    )
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Show fetched secrets and generated compose file (use with --dry-run)",
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Clean up old secrets from tmpfs"
    )
    parser.add_argument(
        "--fetch-secrets-only",
        action="store_true",
        help="Only fetch secrets without starting services (used by systemd on boot)",
    )
    parser.add_argument(
        "--service-account-key",
        default=GCP_SERVICE_ACCOUNT_KEY,
        help=f"Path to GCP service account key (default: {GCP_SERVICE_ACCOUNT_KEY})",
    )

    args = parser.parse_args()

    ensure_podman_secrets_service()

    # Handle cleanup
    if args.cleanup:
        print("Cleaning up old secrets from tmpfs...")
        cleanup_old_secrets()
        print("✅ Cleanup complete\n")
        return
    if args.fetch_secrets_only:
        fetch_all_secrets(args.base_dir, args.project_id, args.service_account_key)
        return

    # Discover all projects
    all_projects = discover_compose_projects(args.base_dir)

    # Handle list
    if args.list:
        list_projects(args.base_dir)
        return

    # Determine which projects to manage
    if args.all:
        # Get all enabled projects
        projects_to_manage = {
            name: info
            for name, info in all_projects.items()
            if info["config"]["enabled"]
        }
        if not projects_to_manage:
            print("No enabled projects found.")
            print("Tip: Use --list to see all projects")
            return
    elif args.projects:
        # Get specific projects
        projects_to_manage = {}
        for project_name in args.projects:
            if project_name in all_projects:
                if not all_projects[project_name]["config"]["enabled"]:
                    print(
                        f"⚠️  Warning: Project '{project_name}' is disabled (x-config.enabled=false)"
                    )
                    response = input(f"   Start it anyway? (y/N): ")
                    if response.lower() != "y":
                        continue
                projects_to_manage[project_name] = all_projects[project_name]
            else:
                print(f"❌ Project not found: {project_name}")
                print(f"   Available projects: {', '.join(all_projects.keys())}")
    else:
        parser.print_help()
        print("\n💡 Tip: Use --list to see all available projects")
        return

    if not projects_to_manage:
        print("No projects to manage.")
        return

    # Activate GCP service account
    if not args.dry_run or args.show_secrets:
        try:
            activate_gcp_service_account(args.service_account_key, args.project_id)
        except Exception as e:
            print(f"❌ Failed to activate GCP service account: {e}")
            return

    # Create necessary directory
    config_dir = os.path.expanduser("~/.config/containers/systemd/")
    mkdir_p(config_dir)

    # Manage each project
    all_services = []
    for project_name, project_info in projects_to_manage.items():
        services = manage_project(
            project_name, project_info, args.project_id, args.dry_run, args.show_secrets
        )
        all_services.extend(services)

    if args.dry_run:
        print("\n" + "=" * 80)
        print("[DRY RUN] No changes made")
        print("=" * 80 + "\n")
        return

    # Reload systemd
    print("\n" + "=" * 80)
    print("Reloading systemd daemon...")
    reload_systemd()
    print("✅ Systemd reloaded")

    # Start services
    all_services = list(set(all_services))  # Remove duplicates
    if all_services:
        print(f"\nStarting {len(all_services)} service(s)...")
        for service_name in all_services:
            start_service(service_name)

    print("\nEnabling secrets loader service...")
    subprocess.run(
        "systemctl --user enable podman-secrets-loader.service",
        shell=True,
        check=True,
    )

    print("✅ Secrets loader enabled")
    # Enable linger
    print("\nEnabling linger...")
    enable_linger()
    print("✅ Linger enabled")

    print("\n" + "=" * 80)
    print("✓ All operations completed successfully!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    """
    # Start specific projects
    python3 update_systemd.py kener postgres

    # Start all enabled projects
    python3 update_systemd.py --all

    # Dry run (show what would happen)
    python3 update_systemd.py --all --dry-run

    # Dry run with secret preview
    python3 update_systemd.py --all --dry-run --show-secrets

    # Use different GCP project
    python3 update_systemd.py --all --project-id my-other-project

    # Use different base directory
    python3 update_systemd.py --all --base-dir ~/my-compose-files

    # Clean up old secrets
    python3 update_systemd.py --cleanup
    """
    main()
