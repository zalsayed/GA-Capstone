import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)

for _path in (_PARENT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)
