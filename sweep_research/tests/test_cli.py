import subprocess
import sys


def test_unimplemented_later_stage():
    result = subprocess.run([sys.executable, "-m", "sweep_research", "run", "--stage", "s2",
                             "--config", "sweep_research/config/xauusd.yaml"],
                            capture_output=True, text=True)
    assert result.returncode == 2
