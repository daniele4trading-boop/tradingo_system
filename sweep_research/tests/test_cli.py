import subprocess
import sys


def test_unimplemented_stage():
    result = subprocess.run([sys.executable, "-m", "sweep_research", "run", "--stage", "s1",
                             "--config", "sweep_research/config/xauusd.yaml"],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "non implementato" in result.stdout
