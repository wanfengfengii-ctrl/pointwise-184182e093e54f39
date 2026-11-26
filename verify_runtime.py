"""运行清单 verify 的单一入口。

依次执行：
1. python3 -B check_project.py —— 完整原回归（发现 tests/ 下全部用例，含换版套件）；
2. python3 -B -m tests.rollout_demo --verify —— 经真实环回 HTTP 断言直切拒收、
   扣留回执后的断台续办、各演练分支与左3右7最终屏态。

任一步失败即以非零码退出。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_step(title, argv):
    print(f'VERIFY_STEP {title}: {" ".join(argv)}', flush=True)
    result = subprocess.run(argv, cwd=ROOT)
    if result.returncode != 0:
        print(f'VERIFY_FAILED {title} exit={result.returncode}', flush=True)
        raise SystemExit(result.returncode or 1)


def main():
    run_step('original-regression', [sys.executable, '-B', 'check_project.py'])
    run_step('rollout-http-assertions',
             [sys.executable, '-B', '-m', 'tests.rollout_demo', '--verify'])
    print('VERIFY_RUNTIME result=PASS original_regression=PASS rollout=PASS', flush=True)


if __name__ == '__main__':
    main()
