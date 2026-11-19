#!/usr/bin/env bash
# TWB v1.1 — macOS/Linux Setup Launcher
# Runs the cross-platform Python setup script with platform-specific enhancements

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# Colors & UI
# ──────────────────────────────────────────────────────────────────────

if [[ -t 1 ]] && [[ "${TERM:-}" != "dumb" ]]; then
    RED=$'\033[91m'
    GREEN=$'\033[92m'
    YELLOW=$'\033[93m'
    BLUE=$'\033[94m'
    CYAN=$'\033[96m'
    MAGENTA=$'\033[95m'
    BOLD=$'\033[1m'
    RESET=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; MAGENTA=""; BOLD=""; RESET=""
fi

print_header() {
    echo
    echo "${BOLD}${CYAN}======================================================================${RESET}"
    echo "${BOLD}${CYAN}  $1${RESET}"
    echo "${BOLD}${CYAN}======================================================================${RESET}"
    echo
}

print_step() { echo "${BLUE}▶ $1${RESET}"; }
print_success() { echo "${GREEN}✓ $1${RESET}"; }
print_warning() { echo "${YELLOW}⚠ $1${RESET}"; }
print_error() { echo "${RED}✗ $1${RESET}"; }
print_info() { echo "${CYAN}ℹ $1${RESET}"; }
print_cmd() { echo "${MAGENTA}  $ $*${RESET}"; }

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

check_python_version() {
    if ! command -v python3 &>/dev/null; then
        print_error "python3 not found in PATH"
        if [[ "$(uname)" == "Darwin" ]]; then
            print_info "Install with: brew install python@3.11"
        else
            print_info "Install with: apt update && apt install python3.11 python3.11-venv"
        fi
        return 1
    fi
    
    local version
    version=$(python3 --version 2>&1 | sed 's/Python //')
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    
    if [[ $major -gt 3 ]] || [[ $major -eq 3 && $minor -ge 11 ]]; then
        print_success "Python $version OK"
        return 0
    else
        print_error "Python 3.11+ required, found $version"
        return 1
    fi
}

check_venv() {
    if [[ ! -x "$VENV_PYTHON" ]]; then
        return 1
    fi
    
    local version
    version=$("$VENV_PYTHON" -c "import sys; print(sys.version.split()[0])" 2>/dev/null) || return 1
    print_success "Virtual environment OK: $version"
    return 0
}

create_venv() {
    print_step "Creating virtual environment..."
    if python3 -m venv "$VENV_DIR"; then
        print_success "Virtual environment created"
        return 0
    else
        print_error "Failed to create virtual environment"
        return 1
    fi
}

install_requirements() {
    print_step "Installing Python requirements..."
    if "$VENV_PIP" install --upgrade pip && "$VENV_PIP" install -r "${SCRIPT_DIR}/requirements.txt"; then
        print_success "Requirements installed"
        return 0
    else
        print_error "Failed to install requirements"
        return 1
    fi
}

check_nvidia_key() {
    if [[ -n "${NVIDIA_API_KEY:-}" ]]; then
        print_success "NVIDIA_API_KEY found: ${NVIDIA_API_KEY:0:12}..."
        return 0
    fi
    print_warning "NVIDIA_API_KEY not set in environment"
    return 1
}

prompt_nvidia_key() {
    print_info "Get your key at https://build.nvidia.com (free tier available)"
    read -rsp "Enter NVIDIA_API_KEY (or press Enter to skip for offline mode): " key
    echo
    if [[ -n "$key" ]]; then
        export NVIDIA_API_KEY="$key"
        # Persist to shell profile
        local shell_rc
        if [[ "$SHELL" == */zsh ]]; then
            shell_rc="$HOME/.zshrc"
        elif [[ "$SHELL" == */bash ]]; then
            shell_rc="$HOME/.bashrc"
        else
            shell_rc="$HOME/.profile"
        fi
        if [[ -f "$shell_rc" ]] && ! grep -q "NVIDIA_API_KEY" "$shell_rc"; then
            echo "export NVIDIA_API_KEY=\"$key\"" >> "$shell_rc"
            print_info "Added to $shell_rc"
        fi
        print_success "NVIDIA_API_KEY set for this session"
        return 0
    fi
    print_warning "Proceeding without NIM — will use offline stand-ins"
    return 1
}

check_samplefiles() {
    local count
    count=$(find "${SCRIPT_DIR}/samplefiles" -name "*.pdf" 2>/dev/null | wc -l)
    if [[ $count -gt 0 ]]; then
        print_success "Found $count PDF(s) in samplefiles/"
        return 0
    fi
    print_warning "No PDFs found in samplefiles/ — add source documents"
    return 1
}

check_keys_dir() {
    if [[ -f "${SCRIPT_DIR}/keys/root.pub" && -f "${SCRIPT_DIR}/keys/ci.pub" ]]; then
        print_success "Signing keys found"
        return 0
    fi
    print_warning "Signing keys not found — will generate dev keys on first build"
    return 1
}

generate_dev_keys() {
    print_step "Generating development Ed25519 keys..."
    if "$VENV_PYTHON" -c "
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
import os
os.makedirs('keys', exist_ok=True)
root = ed25519.Ed25519PrivateKey.generate()
root_pub = root.public_key()
with open('keys/root.key', 'wb') as f:
    f.write(root.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
with open('keys/root.pub', 'wb') as f:
    f.write(root_pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
ci = ed25519.Ed25519PrivateKey.generate()
ci_pub = ci.public_key()
with open('keys/ci.key', 'wb') as f:
    f.write(ci.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
with open('keys/ci.pub', 'wb') as f:
    f.write(ci_pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
print('Keys generated')
"; then
        print_success "Development keys generated in keys/"
        return 0
    else
        print_error "Failed to generate keys"
        return 1
    fi
}

check_onnx_models() {
    if [[ -f "${SCRIPT_DIR}/edge/models/embed.onnx" && -f "${SCRIPT_DIR}/edge/models/rerank.onnx" ]]; then
        print_success "ONNX models found in edge/models/"
        return 0
    fi
    print_warning "ONNX models not found — edge device will need them for production"
    print_info "Export with: python -m indexer.export_onnx (requires GPU, one-time)"
    return 1
}

export_onnx_models() {
    print_step "Exporting ONNX models (requires GPU, 5-15 min)..."
    if "$VENV_PIP" install onnx onnxruntime transformers "optimum[exporters]" && \
       "$VENV_PYTHON" -m indexer.export_onnx --model "nvidia/llama-3.2-nv-embedqa-1b-v2" --output "${SCRIPT_DIR}/edge/models/embed.onnx" && \
       "$VENV_PYTHON" -m indexer.export_onnx --model "nvidia/nv-rerank-qa-mistral-4b" --output "${SCRIPT_DIR}/edge/models/rerank.onnx"; then
        print_success "ONNX models exported to edge/models/"
        return 0
    else
        print_error "ONNX export failed"
        return 1
    fi
}

find_latest_pack() {
    local packs_dir="${SCRIPT_DIR}/build/packs"
    if [[ -d "$packs_dir" ]]; then
        find "$packs_dir" -maxdepth 1 -type d -name "kb-*" -printf "%T@ %p\n" 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-
    fi
}

run_build() {
    local mode="${1:-auto}"
    local max_pdfs="${2:-}"
    local enrich_limit="${3:-150}"
    
    print_step "Running pipeline build (mode: $mode)..."
    local cmd=("$VENV_PYTHON" -m pipeline.run --mode "$mode")
    [[ -n "$max_pdfs" ]] && cmd+=(--max-pdfs "$max_pdfs")
    cmd+=(--enrich-limit "$enrich_limit")
    
    print_cmd "${cmd[@]}"
    if "${cmd[@]}"; then
        print_success "Build completed successfully"
        return 0
    else
        print_error "Build failed"
        return 1
    fi
}

run_edge_server() {
    local pack_path="${1:-}"
    local port="${2:-8080}"
    
    print_step "Starting edge server on port $port..."
    local cmd=("$VENV_PYTHON" -m edge.server --port "$port" --dev)
    [[ -n "$pack_path" ]] && cmd+=(--bootstrap "$pack_path")
    
    print_cmd "${cmd[@]}"
    print_info "Server will be at http://localhost:$port/ui/"
    print_info "Press Ctrl+C to stop"
    
    if "${cmd[@]}"; then
        return 0
    else
        print_error "Server failed"
        return 1
    fi
}

run_tests() {
    print_step "Running unit tests..."
    if "$VENV_PYTHON" -m unittest -v tests.test_all tests.test_nim_mock; then
        print_success "Unit tests passed"
        return 0
    else
        print_error "Unit tests failed"
        return 1
    fi
}

validate_setup() {
    print_header "Validating Setup"
    local results=()
    
    check_python_version && results+=("Python version:PASS") || results+=("Python version:FAIL")
    check_venv && results+=("Virtual environment:PASS") || results+=("Virtual environment:FAIL")
    
    if [[ -x "$VENV_PYTHON" ]]; then
        if "$VENV_PYTHON" -c "import pdfplumber, numpy, cryptography, jsonschema, yaml" 2>/dev/null; then
            results+=("Core packages:PASS")
        else
            results+=("Core packages:FAIL")
        fi
    else
        results+=("Core packages:FAIL")
    fi
    
    check_samplefiles && results+=("Sample PDFs:PASS") || results+=("Sample PDFs:FAIL")
    check_keys_dir && results+=("Signing keys:PASS") || results+=("Signing keys:FAIL")
    check_onnx_models && results+=("ONNX models:PASS") || results+=("ONNX models:FAIL")
    check_nvidia_key && results+=("NVIDIA_API_KEY:PASS") || results+=("NVIDIA_API_KEY:FAIL")
    
    echo
    local all_pass=true
    for r in "${results[@]}"; do
        local name="${r%:*}"
        local status="${r#*:}"
        if [[ "$status" == "PASS" ]]; then
            echo "  ${GREEN}PASS${RESET}  $name"
        else
            echo "  ${RED}FAIL${RESET}  $name"
            all_pass=false
        fi
    done
    
    if $all_pass; then
        print_success "All checks passed!"
        return 0
    else
        print_warning "Some checks failed"
        return 1
    fi
}

confirm() {
    local prompt="$1 [Y/n] "
    local ans
    read -rp "$prompt" ans
    [[ -z "$ans" || "$ans" =~ ^[Yy] ]]
}

# ──────────────────────────────────────────────────────────────────────
# Role Setup Functions
# ──────────────────────────────────────────────────────────────────────

setup_builder() {
    print_header "Builder Setup"
    check_python_version || return 1
    check_venv || create_venv || return 1
    install_requirements || return 1
    check_nvidia_key || { confirm "Do you have a NVIDIA_API_KEY for live NIM models?" && prompt_nvidia_key; }
    check_samplefiles
    check_keys_dir || { confirm "Generate development signing keys?" && generate_dev_keys; }
    print_header "Builder Setup Complete"
    print_info "Run a build with: python -m pipeline.run --mode auto"
    return 0
}

setup_edge_device() {
    print_header "Edge Device Setup"
    check_python_version || return 1
    check_venv || create_venv || return 1
    install_requirements || return 1
    check_onnx_models || { confirm "Export ONNX models now? (requires GPU, 5-15 min)" && export_onnx_models; }
    local pack
    pack=$(find_latest_pack)
    if [[ -n "$pack" ]]; then
        print_success "Found pack: $(basename "$pack")"
    else
        print_warning "No pack found — copy from builder or build locally"
    fi
    print_header "Edge Device Setup Complete"
    if [[ -n "$pack" ]]; then
        print_info "Start server: python -m edge.server --dev --bootstrap $pack --port 8080"
    else
        print_info "Start server: python -m edge.server --dev --bootstrap /path/to/kb-pack --port 8080"
    fi
    print_info "UI: http://<this-ip>:8080/ui/"
    return 0
}

setup_client() {
    print_header "Client Setup"
    print_info "Client needs minimal setup — just curl/browser to test edge device"
    if command -v curl &>/dev/null; then
        print_success "curl found"
    else
        print_warning "curl not found — install for CLI testing"
        if [[ "$(uname)" == "Darwin" ]]; then
            print_info "  brew install curl"
        else
            print_info "  apt install curl"
        fi
    fi
    print_header "Client Setup Complete"
    print_info "Test: http://<edge-ip>:8080/ui/"
    print_info 'curl: curl -X POST http://<edge-ip>:8080/search -H "Content-Type: application/json" -d '"'"'{"query":"propofol dose RSI","k":10}'"'"
    return 0
}

setup_all_in_one() {
    print_header "All-in-One Setup"
    check_python_version || return 1
    check_venv || create_venv || return 1
    install_requirements || return 1
    check_nvidia_key || { confirm "Do you have a NVIDIA_API_KEY for live NIM models?" && prompt_nvidia_key; }
    check_samplefiles
    check_keys_dir || { confirm "Generate development signing keys?" && generate_dev_keys; }
    check_onnx_models || { confirm "Export ONNX models for edge runtime? (requires GPU)" && export_onnx_models; }
    print_header "All-in-One Setup Complete"
    print_info "1. Build: python -m pipeline.run --mode auto"
    print_info "2. Serve: python -m edge.server --dev --bootstrap build/packs/kb-* --port 8080"
    print_info "3. Open:  http://localhost:8080/ui/"
    return 0
}

# ──────────────────────────────────────────────────────────────────────
# Main Menu
# ──────────────────────────────────────────────────────────────────────

show_menu() {
    print_header "TWB v1.1 — Offline Medical Q&A Pipeline Setup" >&2
    echo "${BOLD}Platform:${RESET} $(uname -srm)" >&2
    echo "${BOLD}Repo:${RESET} $SCRIPT_DIR" >&2
    echo >&2
    
    local roles=(
        "builder:Builder (Cloud Build):Runs full pipeline with NIM models to produce signed SQLite packs"
        "edge-device:Edge Device (Runtime):Serves signed packs via stdlib HTTP server with ONNX models"
        "client:Client (Test Console):Tests edge device via HTTP API (curl/browser/UI)"
        "all-in-one:All-in-One (Builder + Edge + Client):Everything on one machine for development/testing"
    )
    
    echo "${BOLD}Select Role to Setup:${RESET}" >&2
    local i=1
    for role in "${roles[@]}"; do
        IFS=':' read -r key name desc <<< "$role"
        echo "  $i. $name — $desc" >&2
        ((i++))
    done
    
    local role_idx
    read -rp "Choice [1-${#roles[@]}] (default 1): " role_idx >&2
    role_idx=${role_idx:-1}
    if [[ $role_idx -lt 1 || $role_idx -gt ${#roles[@]} ]]; then
        role_idx=1
    fi
    local selected_role
    selected_role=$(echo "${roles[$((role_idx-1))]}" | cut -d: -f1)
    
    echo >&2
    echo "${BOLD}Actions:${RESET}" >&2
    local actions=(
        "Setup this role"
        "Run build (builder/all-in-one)"
        "Start edge server (edge-device/all-in-one)"
        "Run tests"
        "Validate setup"
        "Exit"
    )
    
    for i in "${!actions[@]}"; do
        echo "  $((i+1)). ${actions[i]}" >&2
    done
    
    local action_idx
    read -rp "Choice [1-${#actions[@]}]: " action_idx >&2
    if [[ -z "$action_idx" || $action_idx -lt 1 || $action_idx -gt ${#actions[@]} ]]; then
        return 1
    fi
    
    local selected_action="${actions[$((action_idx-1))]}"
    echo "$selected_role $selected_action"
}

# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

main() {
    while true; do
        local selection
        selection=$(show_menu) || continue
        local role action
        role=$(echo "$selection" | cut -d' ' -f1)
        action=$(echo "$selection" | cut -d' ' -f2-)

        if [[ "$action" == "Exit" ]]; then
            print_info "Goodbye!"
            break
        fi
        
        local success=0
        case "$action" in
            "Setup this role")
                case "$role" in
                    builder) setup_builder && success=1 ;;
                    edge-device) setup_edge_device && success=1 ;;
                    client) setup_client && success=1 ;;
                    all-in-one) setup_all_in_one && success=1 ;;
                esac
                ;;
            "Run build (builder/all-in-one)")
                if [[ "$role" != "builder" && "$role" != "all-in-one" ]]; then
                    print_error "Build only available for builder/all-in-one roles"
                else
                    local mode="auto"
                    check_nvidia_key || mode="offline"
                    echo "Build mode: 1=auto, 2=nim, 3=offline (default: $mode)"
                    read -rp "Choice [1-3]: " mode_choice
                    case "${mode_choice:-1}" in
                        1) mode="auto" ;;
                        2) mode="nim" ;;
                        3) mode="offline" ;;
                    esac
                    read -rp "Max PDFs (Enter for all): " max_pdfs
                    read -rp "Enrichment limit (default 150): " enrich_limit
                    enrich_limit=${enrich_limit:-150}
                    run_build "$mode" "$max_pdfs" "$enrich_limit" && success=1
                fi
                ;;
            "Start edge server (edge-device/all-in-one)")
                if [[ "$role" != "edge-device" && "$role" != "all-in-one" ]]; then
                    print_error "Edge server only for edge-device/all-in-one roles"
                else
                    local pack
                    pack=$(find_latest_pack)
                    local pack_path=""
                    if [[ -n "$pack" ]] && confirm "Use latest pack: $(basename "$pack")?"; then
                        pack_path="$pack"
                    elif [[ -z "$pack" ]]; then
                        read -rp "Pack path (Enter for auto-discover): " pack_path
                    fi
                    read -rp "Port (default 8080): " port
                    port=${port:-8080}
                    run_edge_server "$pack_path" "$port" && success=1
                fi
                ;;
            "Run tests")
                run_tests && success=1
                ;;
            "Validate setup")
                validate_setup && success=1
                ;;
        esac
        
        if [[ $success -eq 0 && "$action" != "Exit" ]]; then
            print_error "Action failed: $action"
        fi
        
        read -rp $'\n'"Press Enter to continue..." _
    done
}

main "$@"