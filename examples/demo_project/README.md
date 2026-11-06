# RunFix demo project

Try inspection-only mode first:

```powershell
runfix agent . 
```

Then authorize the test suite:

```powershell
runfix agent . --allow-project-code
```

The second command should discover `tests/test_demo.py`, run pytest, and report `verified` if it passes.
