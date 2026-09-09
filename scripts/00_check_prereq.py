#!/usr/bin/env python3
"""
Pre-deployment prerequisite checker for Qwen3.8-27B × vLLM 0.28.0 on DBR 19

Checks:
  - NVIDIA driver version (≥ 580)
  - GPU specs (name, memory ≥ 80GB, compute capability 8.0)
  - /local_disk0 free space (≥ 45 GB)
  - uv tool availability
  - Python package versions (torch, transformers)
  - Environment variables (OPENSSL*, FIPS*, PYTHONPATH, LD_LIBRARY_PATH)
  - Network reachability (PyPI, HuggingFace)

Exit code: 0 if all PASS, 1 if any FAIL
"""

import os
import sys
import subprocess
import shutil
import urllib.request
import socket
from pathlib import Path
from typing import Dict, List, Tuple, Any

# ANSI colors (simple text format fallback for all terminals)
class Colors:
    PASS = "[PASS]"
    FAIL = "[FAIL]"
    INFO = "[INFO]"
    WARN = "[WARN]"
    RESET = ""


def run_command(cmd: List[str], timeout: int = 10) -> Tuple[int, str, str]:
    """Execute a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timeout ({timeout}s)"
    except FileNotFoundError:
        return -1, "", f"명령을 찾을 수 없습니다: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def check_nvidia_driver() -> Dict[str, Any]:
    """Check NVIDIA driver version and GPU specs."""
    results = {}
    rc, stdout, stderr = run_command(["nvidia-smi", "--query-gpu=driver_version,name,memory.total,compute_cap", "--format=csv,noheader,nounits"])

    if rc != 0:
        results["driver_version"] = (Colors.FAIL, "nvidia-smi 사용 불가", "")
        results["gpu_name"] = (Colors.FAIL, "nvidia-smi 사용 불가", "")
        results["gpu_memory"] = (Colors.FAIL, "nvidia-smi 사용 불가", "")
        results["compute_capability"] = (Colors.FAIL, "nvidia-smi 사용 불가", "")
        return results

    try:
        lines = stdout.strip().split("\n")
        if not lines or not lines[0]:
            raise ValueError("Empty nvidia-smi output")

        # Handle multiple GPUs (we only check the first one)
        first_gpu = lines[0].split(",")
        driver_version = first_gpu[0].strip()
        gpu_name = first_gpu[1].strip()
        gpu_memory_mb = first_gpu[2].strip()
        compute_cap = first_gpu[3].strip()

        # Parse driver version
        try:
            driver_major = int(driver_version.split(".")[0])
            if driver_major >= 580:
                results["driver_version"] = (Colors.PASS, driver_version, "")
            else:
                results["driver_version"] = (Colors.FAIL, f"{driver_version} (need ≥580)", "")
        except (ValueError, IndexError):
            results["driver_version"] = (Colors.FAIL, driver_version, "버전을 해석할 수 없습니다")

        # GPU name
        results["gpu_name"] = (Colors.INFO, gpu_name, "")

        # Parse GPU memory
        try:
            gpu_memory_gb = float(gpu_memory_mb) / 1024.0
            if gpu_memory_gb >= 80.0:
                results["gpu_memory"] = (Colors.PASS, f"{gpu_memory_gb:.1f} GB", "")
            else:
                results["gpu_memory"] = (Colors.FAIL, f"{gpu_memory_gb:.1f} GB (need ≥80GB)", "")
        except ValueError:
            results["gpu_memory"] = (Colors.FAIL, gpu_memory_mb, "메모리 값을 해석할 수 없습니다")

        # Compute capability
        if "8.0" in compute_cap or compute_cap == "80":
            results["compute_capability"] = (Colors.PASS, "8.0 (A100)", "")
        else:
            results["compute_capability"] = (Colors.FAIL, compute_cap, "Expected 8.0 (A100)")

    except Exception as e:
        results["driver_version"] = (Colors.FAIL, "Error parsing output", str(e))
        results["gpu_name"] = (Colors.FAIL, "Error parsing output", str(e))
        results["gpu_memory"] = (Colors.FAIL, "Error parsing output", str(e))
        results["compute_capability"] = (Colors.FAIL, "Error parsing output", str(e))

    return results


def check_local_disk0() -> Dict[str, Any]:
    """Check /local_disk0 free space."""
    results = {}
    path = Path("/local_disk0")

    if not path.exists():
        results["disk_space"] = (Colors.FAIL, "없음", "/local_disk0 경로가 존재하지 않습니다")
        return results

    try:
        stat = os.statvfs(str(path))
        free_bytes = stat.f_bavail * stat.f_frsize
        free_gb = free_bytes / (1024 ** 3)

        if free_gb >= 45.0:
            results["disk_space"] = (Colors.PASS, f"{free_gb:.1f} GB", "")
        else:
            results["disk_space"] = (Colors.FAIL, f"{free_gb:.1f} GB (need ≥45GB)", "")
    except Exception as e:
        results["disk_space"] = (Colors.FAIL, "Error", str(e))

    return results


def check_uv_tool() -> Dict[str, Any]:
    """Check if 'uv' tool is available."""
    results = {}
    uv_path = Path("/usr/local/bin/uv")

    if uv_path.exists():
        try:
            rc, stdout, stderr = run_command(["uv", "--version"], timeout=5)
            if rc == 0:
                version = stdout.strip().split("\n")[0]
                results["uv_tool"] = (Colors.PASS, version, "")
            else:
                results["uv_tool"] = (Colors.FAIL, "존재하지만 실행 권한이 없습니다", stderr)
        except Exception as e:
            results["uv_tool"] = (Colors.FAIL, "Found but error running", str(e))
    else:
        results["uv_tool"] = (Colors.FAIL, "/usr/local/bin/uv 에 없습니다", "")

    return results


def check_python_packages() -> Dict[str, Any]:
    """Check torch and transformers versions (information only)."""
    results = {}

    try:
        import torch
        results["torch_version"] = (Colors.INFO, str(torch.__version__), "")
    except ImportError:
        results["torch_version"] = (Colors.FAIL, "미설치", "")
    except Exception as e:
        results["torch_version"] = (Colors.FAIL, "Error", str(e))

    try:
        import transformers
        results["transformers_version"] = (Colors.INFO, str(transformers.__version__), "")
    except ImportError:
        results["transformers_version"] = (Colors.FAIL, "미설치", "")
    except Exception as e:
        results["transformers_version"] = (Colors.FAIL, "Error", str(e))

    return results


def check_environment_variables() -> Dict[str, Any]:
    """Check environment variables (OPENSSL*, FIPS*, PYTHONPATH, LD_LIBRARY_PATH)."""
    results = {}

    # Check OPENSSL* and FIPS* variables
    sensitive_vars = {k: v for k, v in os.environ.items() if k.startswith(("OPENSSL", "FIPS"))}
    if sensitive_vars:
        var_list = ", ".join(sensitive_vars.keys())
        results["sensitive_env"] = (
            Colors.WARN,
            f"Found: {var_list}",
            "These must be stripped before venv activation"
        )
    else:
        results["sensitive_env"] = (Colors.PASS, "없음", "")

    # PYTHONPATH
    pythonpath = os.environ.get("PYTHONPATH", "(설정되지 않음)")
    results["pythonpath"] = (Colors.INFO, pythonpath[:60] + ("..." if len(str(pythonpath)) > 60 else ""), "")

    # LD_LIBRARY_PATH
    ld_library_path = os.environ.get("LD_LIBRARY_PATH", "(설정되지 않음)")
    results["ld_library_path"] = (Colors.INFO, ld_library_path[:60] + ("..." if len(str(ld_library_path)) > 60 else ""), "")

    return results


def check_network_reachability() -> Dict[str, Any]:
    """Check PyPI and HuggingFace reachability."""
    results = {}

    # PyPI
    try:
        req = urllib.request.Request("https://pypi.org", method="HEAD")
        urllib.request.urlopen(req, timeout=5)
        results["pypi_reachable"] = (Colors.PASS, "https://pypi.org", "")
    except urllib.error.URLError as e:
        results["pypi_reachable"] = (Colors.FAIL, "pypi.org 에 도달할 수 없습니다", str(e.reason))
    except socket.timeout:
        results["pypi_reachable"] = (Colors.FAIL, "Timeout", "")
    except Exception as e:
        results["pypi_reachable"] = (Colors.FAIL, "Error", str(e))

    # HuggingFace
    try:
        req = urllib.request.Request("https://huggingface.co", method="HEAD")
        urllib.request.urlopen(req, timeout=5)
        results["hf_reachable"] = (Colors.PASS, "https://huggingface.co", "")
    except urllib.error.URLError as e:
        results["hf_reachable"] = (
            Colors.FAIL,
            "huggingface.co 에 도달할 수 없습니다",
            "If blocked, use UC Volume path instead (see § Install)"
        )
    except socket.timeout:
        results["hf_reachable"] = (
            Colors.FAIL,
            "Timeout",
            "If blocked, use UC Volume path instead (see § Install)"
        )
    except Exception as e:
        results["hf_reachable"] = (Colors.FAIL, "Error", str(e))

    return results


def print_results_table(all_results: Dict[str, Dict[str, Any]]):
    """Print results as a formatted table."""
    print("\n" + "="*90)
    print(f"{'검사항목':<35} {'상태':<12} {'값':<35} {'설명':<10}")
    print("="*90)

    failed_checks = []

    for section_name, section_results in all_results.items():
        for check_name, (status, value, note) in section_results.items():
            status_display = status
            if status == Colors.FAIL:
                failed_checks.append(f"{section_name}/{check_name}")

            # Truncate long values
            value_str = str(value)[:35]
            note_str = str(note)[:40]

            print(f"{check_name:<35} {status_display:<12} {value_str:<35} {note_str:<10}")

    print("="*90)

    if failed_checks:
        print(f"\n{Colors.FAIL} 실패한 검사:")
        for check in failed_checks:
            print(f"  - {check}")
        return 1
    else:
        print(f"\n{Colors.PASS} 모든 필수 조건 확인 완료. 진행 가능합니다.")
        return 0


def main():
    """Main entry point."""
    print("\n" + "="*90)
    print("Qwen3.8-27B × vLLM 배포 전 환경 점검")
    print("="*90)

    all_results = {}

    # Run all checks
    all_results["GPU & Driver"] = check_nvidia_driver()
    all_results["Disk"] = check_local_disk0()
    all_results["Tools"] = check_uv_tool()
    all_results["Python Packages"] = check_python_packages()
    all_results["Environment"] = check_environment_variables()
    all_results["Network"] = check_network_reachability()

    # Print results and determine exit code
    exit_code = print_results_table(all_results)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
