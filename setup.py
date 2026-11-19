#!/usr/bin/env python3
"""
TWB v1.1 — Cross-Platform Interactive Setup Script
Supports: macOS, Linux, Windows
Roles: builder, edge-device, client, all-in-one
"""

import os
import sys
import subprocess
import platform
import shutil
import json
import getpass
from pathlib import Path
from typing import Optional, List, Dict, Any

# ──────────────────────────────────────────────────────────────────────
# Constants & Configuration
# ──────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.resolve()
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
SAMPLEFILES_DIR = REPO_ROOT / "samplefiles"
KEYS_DIR = REPO_ROOT / "keys"
BUILD_DIR = REPO_ROOT / "build"
EDGE_MODELS_DIR = REPO_ROOT / "edge" / "models"

ROLES = {
    "builder": {
        "name": "Builder (Cloud Build)",
        "description": "Runs full pipeline with NIM models to produce signed SQLite packs",
        "requires_nim": True,
        "requires_gpu": False,
        "components": ["pipeline", "ingest", "enrich", "indexer", "eval", "signing"],
    },
    "edge-device": {
        "name": "Edge Device (Runtime)",
        "description": "Serves signed packs via stdlib HTTP server with ONNX models",
        "requires_nim": False,
        "requires_gpu": False,
        "components": ["edge.server", "edge.core", "onnx-runtime"],
    },
    "client": {
        "name": "Client (Test Console)",
        "description": "Tests edge device via HTTP API (curl/browser/UI)",
        "requires_nim": False,
        "requires_gpu": False,
        "components": ["curl", "browser"],
    },
    "all-in-one": {
        "name": "All-in-One (Builder + Edge + Client)",
        "description": "Everything on one machine for development/testing",
        "requires_nim": True,
        "requires_gpu": False,
        "components": ["all"],
    },
}

SYSTEM = platform.system().lower()
IS_WINDOWS = SYSTEM == "windows"
IS_MAC = SYSTEM == "darwin"
IS_LINUX = SYSTEM == "python"

PYTHON_MIN_VERSION = (3, 11)
VENV_DIR = REPO_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
VENV_PIP = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("pip.exe" if IS_WINDOWS else "pip")

# ──────────────────────────────────────────────────────────────────────
# UI Helpers
# ──────────────────────────────────────────────────────────────────────

class Colors:
    if IS_WINDOWS:
        RED = YELLOW = GREEN = BLUE = CYAN = MAGENTA = BOLD = RESET = ""
    else:
        RED = "\033[91m"
        YELLOW = "\033[93m"
        GREEN = "\033[92m"
        BLUE = "\033[94m"
        CYAN = "\033[96m"
        MAGENTA = "\033[95m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}  {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.RESET}\n")

def print_step(text: str):
    print(f"{Colors.BLUE}▶ {text}{Colors.RESET}")

def print_success(text: str):
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")

def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")

def print_error(text: str):
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")

def print_info(text: str):
    print(f"{Colors.CYAN}ℹ {text}{Colors.RESET}")

def prompt_yes_no(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        ans = input(f"{Colors.CYAN}{question}{suffix}: {Colors.RESET}").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer yes or no.")

def prompt_choice(question: str, choices: List[str], default: int = 0) -> int:
    print(f"{Colors.CYAN}{question}{Colors.RESET}")
    for i, c in enumerate(choices):
        marker = "→" if i == default else " "
        print(f"  {marker} {i + 1}. {c}")
    while True:
        ans = input(f"{Colors.CYAN}Choice [1-{len(choices)}] (default {default + 1}): {Colors.RESET}").strip()
        if not ans:
            return default
        try:
            idx = int(ans) - 1
            if 0 <= idx < len(choices):
                return idx
        except ValueError:
            pass
        print(f"Please enter a number 1-{len(choices)}.")

def prompt_text(question: str, default: str = "", secret: bool = False) -> str:
    if secret:
        ans = getpass.getpass(f"{Colors.CYAN}{question}: {Colors.RESET}")
    else:
        suffix = f" (default: {default})" if default else ""
        ans = input(f"{Colors.CYAN}{question}{suffix}: {Colors.RESET}").strip()
    return ans or default

def run_cmd(cmd: List[str], cwd: Optional[Path] = None, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run command with nice output."""
    display = " ".join(cmd)
    print(f"  {Colors.MAGENTA}$ {display}{Colors.RESET}")
    try:
        result = subprocess.run(cmd, cwd=cwd, check=check, capture_output=capture, text=True)
        if capture:
            return result
        return result
    except subprocess.CalledProcessError as e:
        if capture:
            print(f"  {Colors.RED}stderr: {e.stderr}{Colors.RESET}")
        raise

def check_python_version() -> bool:
    """Verify Python version meets minimum."""
    version = sys.version_info[:2]
    if version < PYTHON_MIN_VERSION:
        print_error(f"Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ required, found {version[0]}.{version[1]}")
        return False
    print_success(f"Python {version[0]}.{version[1]} OK")
    return True

def check_venv() -> bool:
    """Check if virtual environment exists and is valid."""
    if not VENV_PYTHON.exists():
        return False
    try:
        result = run_cmd([str(VENV_PYTHON), "-c", "import sys; print(sys.version)"], capture=True, check=True)
        print_success(f"Virtual environment OK: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError:
        return False

def create_venv() -> bool:
    """Create virtual environment."""
    print_step("Creating virtual environment...")
    try:
        run_cmd([sys.executable, "-m", "venv", str(VENV_DIR)])
        print_success("Virtual environment created")
        return True
    except subprocess.CalledProcessError:
        print_error("Failed to create virtual environment")
        return False

def install_requirements() -> bool:
    """Install Python requirements."""
    print_step("Installing Python requirements...")
    try:
        # Upgrade pip first
        run_cmd([str(VENV_PIP), "install", "--upgrade", "pip"])
        # Install requirements
        run_cmd([str(VENV_PIP), "install", "-r", str(REQUIREMENTS_FILE)])
        print_success("Requirements installed")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to install requirements: {e}")
        return False

def check_nvidia_key() -> Optional[str]:
    """Check for NVIDIA_API_KEY in environment."""
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if key:
        print_success(f"NVIDIA_API_KEY found: {key[:12]}...")
        return key
    print_warning("NVIDIA_API_KEY not set in environment")
    return None

def prompt_nvidia_key() -> Optional[str]:
    """Prompt user for NVIDIA API key."""
    print_info("Get your key at https://build.nvidia.com (free tier available)")
    key = prompt_text("Enter NVIDIA_API_KEY (or press Enter to skip for offline mode)", secret=True)
    if key:
        os.environ["NVIDIA_API_KEY"] = key
        print_success("NVIDIA_API_KEY set for this session")
        return key
    print_warning("Proceeding without NIM — will use offline stand-ins")
    return None

def check_samplefiles() -> bool:
    """Verify sample PDFs exist."""
    pdfs = list(SAMPLEFILES_DIR.glob("**/*.pdf"))
    if pdfs:
        print_success(f"Found {len(pdfs)} PDF(s) in samplefiles/")
        return True
    print_warning("No PDFs found in samplefiles/ — you'll need to add source documents")
    return False

def check_keys_dir() -> bool:
    """Check for Ed25519 signing keys."""
    if KEYS_DIR.exists():
        root_pub = KEYS_DIR / "root.pub"
        ci_pub = KEYS_DIR / "ci.pub"
        if root_pub.exists() and ci_pub.exists():
            print_success("Signing keys found")
            return True
    print_warning("Signing keys not found — will generate dev keys on first build")
    return False

def generate_dev_keys() -> bool:
    """Generate development signing keys."""
    print_step("Generating development Ed25519 keys...")
    try:
        KEYS_DIR.mkdir(parents=True, exist_ok=True)
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        
        # Root key (offline in production)
        root = ed25519.Ed25519PrivateKey.generate()
        root_pub = root.public_key()
        (KEYS_DIR / "root.key").write_bytes(
            root.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
        )
        (KEYS_DIR / "root.pub").write_bytes(
            root_pub.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        
        # CI key (rotating)
        ci = ed25519.Ed25519PrivateKey.generate()
        ci_pub = ci.public_key()
        (KEYS_DIR / "ci.key").write_bytes(
            ci.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
        )
        (KEYS_DIR / "ci.pub").write_bytes(
            ci_pub.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        
        print_success("Development keys generated in keys/")
        return True
    except Exception as e:
        print_error(f"Failed to generate keys: {e}")
        return False

def check_onnx_models() -> bool:
    """Check for ONNX models on edge device."""
    embed_onnx = EDGE_MODELS_DIR / "embed.onnx"
    rerank_onnx = EDGE_MODELS_DIR / "rerank.onnx"
    
    if embed_onnx.exists() and rerank_onnx.exists():
        print_success("ONNX models found in edge/models/")
        return True
    print_warning("ONNX models not found — edge device will need them for production")
    print_info("Export with: python -m indexer.export_onnx (requires GPU, one-time)")
    return False

def export_onnx_models() -> bool:
    """Export ONNX models (requires GPU, transformers, optimum)."""
    print_step("Exporting ONNX models (requires GPU, may take 5-15 min)...")
    try:
        # Check if export script exists
        export_script = REPO_ROOT / "indexer" / "export_onnx.py"
        if not export_script.exists():
            print_error("export_onnx.py not found — check indexer/")
            return False
        
        # Install export dependencies
        run_cmd([str(VENV_PIP), "install", "onnx", "onnxruntime", "transformers", "optimum[exporters]"])
        
        # Export embedding model
        run_cmd([
            str(VENV_PYTHON), "-m", "indexer.export_onnx",
            "--model", "nvidia/llama-3.2-nv-embedqa-1b-v2",
            "--output", str(EDGE_MODELS_DIR / "embed.onnx")
        ])
        
        # Export rerank model
        run_cmd([
            str(VENV_PYTHON), "-m", "indexer.export_onnx",
            "--model", "nvidia/nv-rerank-qa-mistral-4b",
            "--output", str(EDGE_MODELS_DIR / "rerank.onnx")
        ])
        
        print_success("ONNX models exported to edge/models/")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"ONNX export failed: {e}")
        return False

def run_build(mode: str = "auto", max_pdfs: Optional[int] = None, enrich_limit: int = 150) -> bool:
    """Run the pipeline build."""
    print_step(f"Running pipeline build (mode: {mode})...")
    cmd = [str(VENV_PYTHON), "-m", "pipeline.run", "--mode", mode]
    if max_pdfs:
        cmd.extend(["--max-pdfs", str(max_pdfs)])
    if enrich_limit:
        cmd.extend(["--enrich-limit", str(enrich_limit)])
    try:
        run_cmd(cmd)
        print_success("Build completed successfully")
        return True
    except subprocess.CalledProcessError:
        print_error("Build failed")
        return False

def run_edge_server(pack_path: Optional[Path] = None, port: int = 8080, dev: bool = True) -> bool:
    """Start the edge server."""
    print_step(f"Starting edge server on port {port}...")
    cmd = [str(VENV_PYTHON), "-m", "edge.server", "--port", str(port)]
    if dev:
        cmd.append("--dev")
    if pack_path:
        cmd.extend(["--bootstrap", str(pack_path)])
    print_info(f"Server will be at http://localhost:{port}/ui/")
    print_info("Press Ctrl+C to stop")
    try:
        subprocess.run(cmd, check=True)
        return True
    except KeyboardInterrupt:
        print_info("\nServer stopped")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Server failed: {e}")
        return False

def find_latest_pack() -> Optional[Path]:
    """Find the latest built pack."""
    packs_dir = BUILD_DIR / "packs"
    if not packs_dir.exists():
        return None
    packs = sorted(packs_dir.glob("kb-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return packs[0] if packs else None

# ──────────────────────────────────────────────────────────────────────
# Role Setup Functions
# ──────────────────────────────────────────────────────────────────────

def setup_builder() -> bool:
    """Setup for Builder role."""
    print_header("Builder Setup")
    
    # Check/Install Python
    if not check_python_version():
        return False
    
    # Virtual environment
    if not check_venv():
        if not create_venv():
            return False
    
    # Requirements
    if not install_requirements():
        return False
    
    # NVIDIA API Key
    if not check_nvidia_key():
        if prompt_yes_no("Do you have a NVIDIA_API_KEY for live NIM models?", default=True):
            prompt_nvidia_key()
        else:
            print_warning("Will use offline mode for builds")
    
    # Sample files
    check_samplefiles()
    
    # Keys
    if not check_keys_dir():
        if prompt_yes_no("Generate development signing keys?", default=True):
            generate_dev_keys()
    
    print_header("Builder Setup Complete")
    print_info("Run a build with:")
    print(f"  {Colors.GREEN}python -m pipeline.run --mode auto{Colors.RESET}")
    print_info("Or use the run_test.sh script:")
    print(f"  {Colors.GREEN}./run_test.sh{Colors.RESET}")
    return True

def setup_edge_device() -> bool:
    """Setup for Edge Device role."""
    print_header("Edge Device Setup")
    
    if not check_python_version():
        return False
    
    if not check_venv():
        if not create_venv():
            return False
    
    if not install_requirements():
        return False
    
    # ONNX models
    if not check_onnx_models():
        if prompt_yes_no("Export ONNX models now? (requires GPU, 5-15 min)", default=False):
            export_onnx_models()
    
    # Find pack to serve
    pack = find_latest_pack()
    if pack:
        print_success(f"Found pack: {pack.name}")
    else:
        print_warning("No pack found — you'll need to copy one from builder or build locally")
    
    print_header("Edge Device Setup Complete")
    print_info("Start server with:")
    if pack:
        print(f"  {Colors.GREEN}python -m edge.server --dev --bootstrap {pack} --port 8080{Colors.RESET}")
    else:
        print(f"  {Colors.GREEN}python -m edge.server --dev --bootstrap /path/to/kb-pack --port 8080{Colors.RESET}")
    print_info("UI will be at http://<this-ip>:8080/ui/")
    return True

def setup_client() -> bool:
    """Setup for Client role."""
    print_header("Client Setup")
    print_info("Client role needs minimal setup — just curl/browser to test edge device")
    
    # Check for curl
    if shutil.which("curl"):
        print_success("curl found")
    else:
        print_warning("curl not found — install for CLI testing")
        if IS_MAC:
            print_info("  brew install curl")
        elif IS_WINDOWS:
            print_info("  winget install curl")
        else:
            print_info("  apt install curl")
    
    print_header("Client Setup Complete")
    print_info("Test edge device at http://<edge-ip>:8080/ui/")
    print_info("Or via curl:")
    print(f"  {Colors.GREEN}curl -X POST http://<edge-ip>:8080/search -H 'Content-Type: application/json' -d '{{\"query\":\"propofol dose RSI\",\"k\":10}}'{Colors.RESET}")
    return True

def setup_all_in_one() -> bool:
    """Setup all roles on one machine."""
    print_header("All-in-One Setup")
    
    if not check_python_version():
        return False
    
    if not check_venv():
        if not create_venv():
            return False
    
    if not install_requirements():
        return False
    
    # NVIDIA key
    if not check_nvidia_key():
        if prompt_yes_no("Do you have a NVIDIA_API_KEY for live NIM models?", default=True):
            prompt_nvidia_key()
    
    # Sample files
    check_samplefiles()
    
    # Keys
    if not check_keys_dir():
        if prompt_yes_no("Generate development signing keys?", default=True):
            generate_dev_keys()
    
    # ONNX models (optional for all-in-one)
    if not check_onnx_models():
        if prompt_yes_no("Export ONNX models for edge runtime? (requires GPU)", default=False):
            export_onnx_models()
    
    print_header("All-in-One Setup Complete")
    print_info("Quick test workflow:")
    print(f"  1. Build pack:  {Colors.GREEN}python -m pipeline.run --mode auto{Colors.RESET}")
    print(f"  2. Start edge:  {Colors.GREEN}python -m edge.server --dev --bootstrap build/packs/kb-* --port 8080{Colors.RESET}")
    print(f"  3. Open UI:     {Colors.GREEN}http://localhost:8080/ui/{Colors.RESET}")
    return True

def run_tests() -> bool:
    """Run test suite."""
    print_header("Running Tests")
    if not check_venv():
        print_error("Virtual environment not set up")
        return False
    
    try:
        run_cmd([str(VENV_PYTHON), "-m", "unittest", "-v", "tests.test_all", "tests.test_nim_mock"])
        print_success("Unit tests passed")
    except subprocess.CalledProcessError:
        print_error("Unit tests failed")
        return False
    
    # UI smoke tests (requires server running)
    if prompt_yes_no("Run UI smoke tests? (requires edge server running on :8080)", default=False):
        try:
            run_cmd(["node", "tests/ui_smoke.mjs"])
            print_success("UI smoke tests passed")
        except subprocess.CalledProcessError:
            print_error("UI smoke tests failed")
            return False
    
    return True

def validate_setup() -> bool:
    """Validate the complete setup."""
    print_header("Validating Setup")
    
    checks = []
    
    # Python
    checks.append(("Python version", check_python_version()))
    
    # Venv
    checks.append(("Virtual environment", check_venv()))
    
    # Requirements
    if VENV_PYTHON.exists():
        try:
            run_cmd([str(VENV_PYTHON), "-c", "import pdfplumber, numpy, cryptography, jsonschema, yaml"], capture=True)
            checks.append(("Core packages", True))
        except subprocess.CalledProcessError:
            checks.append(("Core packages", False))
    else:
        checks.append(("Core packages", False))
    
    # Sample files
    checks.append(("Sample PDFs", check_samplefiles()))
    
    # Keys
    checks.append(("Signing keys", check_keys_dir()))
    
    # ONNX
    checks.append(("ONNX models", check_onnx_models()))
    
    # NIM key
    checks.append(("NVIDIA_API_KEY", check_nvidia_key() is not None))
    
    print()
    all_pass = True
    for name, passed in checks:
        status = f"{Colors.GREEN}PASS{Colors.RESET}" if passed else f"{Colors.RED}FAIL{Colors.RESET}"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False
    
    if all_pass:
        print_success("\nAll checks passed!")
    else:
        print_warning("\nSome checks failed — see above")
    
    return all_pass

# ──────────────────────────────────────────────────────────────────────
# Main Interactive Menu
# ──────────────────────────────────────────────────────────────────────

def show_main_menu() -> tuple[str, str]:
    """Display main menu and return selected (role_key, action)."""
    print_header("TWB v1.1 — Offline Medical Q&A Pipeline Setup")
    print(f"{Colors.BOLD}Platform:{Colors.RESET} {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"{Colors.BOLD}Repo:{Colors.RESET} {REPO_ROOT}")
    print()
    
    print(f"{Colors.BOLD}Select Role to Setup:{Colors.RESET}")
    role_choices = [f"{ROLES[k]['name']} — {ROLES[k]['description']}" for k in ROLES]
    role_idx = prompt_choice("", role_choices)
    role_key = list(ROLES.keys())[role_idx]
    
    print()
    print(f"{Colors.BOLD}Actions:{Colors.RESET}")
    actions = [
        "Setup this role",
        "Run build (builder/all-in-one)",
        "Start edge server (edge-device/all-in-one)",
        "Run tests",
        "Validate setup",
        "Exit",
    ]
    action_idx = prompt_choice("", actions)
    action = actions[action_idx]
    
    return role_key, action

def main():
    """Main entry point."""
    try:
        while True:
            role_key, action = show_main_menu()
            
            if action == "Exit":
                print_info("Goodbye!")
                break
            
            success = False
            
            if action == "Setup this role":
                if role_key == "builder":
                    success = setup_builder()
                elif role_key == "edge-device":
                    success = setup_edge_device()
                elif role_key == "client":
                    success = setup_client()
                elif role_key == "all-in-one":
                    success = setup_all_in_one()
            
            elif action == "Run build (builder/all-in-one)":
                if role_key not in ("builder", "all-in-one"):
                    print_error("Build only available for builder/all-in-one roles")
                else:
                    mode = "auto"
                    if check_nvidia_key():
                        mode = "auto"
                    else:
                        mode = prompt_choice("Build mode?", ["auto (try NIM, fallback offline)", "nim (require NIM)", "offline (no network)"])
                        mode = ["auto", "nim", "offline"][mode]
                    max_pdfs = None
                    if prompt_yes_no("Limit number of PDFs for quick test?", default=False):
                        max_pdfs = int(prompt_text("Max PDFs", default="5"))
                    enrich_limit = int(prompt_text("Enrichment limit (NIM chat calls)", default="150"))
                    success = run_build(mode, max_pdfs, enrich_limit)
            
            elif action == "Start edge server (edge-device/all-in-one)":
                if role_key not in ("edge-device", "all-in-one"):
                    print_error("Edge server only for edge-device/all-in-one roles")
                else:
                    pack = find_latest_pack()
                    if pack:
                        use_latest = prompt_yes_no(f"Use latest pack: {pack.name}?", default=True)
                        if not use_latest:
                            pack = None
                    if not pack:
                        pack_str = prompt_text("Pack path (or Enter for --dev auto-discover)")
                        pack = Path(pack_str) if pack_str else None
                    port = int(prompt_text("Port", default="8080"))
                    success = run_edge_server(pack, port)
            
            elif action == "Run tests":
                success = run_tests()
            
            elif action == "Validate setup":
                success = validate_setup()
            
            if not success and action != "Exit":
                print_error(f"Action failed: {action}")
            
            input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.RESET}")
    
    except KeyboardInterrupt:
        print_info("\n\nInterrupted. Goodbye!")
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()