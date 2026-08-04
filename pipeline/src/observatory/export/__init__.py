"""Export layer: the dashboard's static data files.

    from observatory.export import shards

``shards``      per-municipality columnar shards (Task 8)
``aggregates``  summary / states / anomalies / index   (Task 9)
``manifest``    content hashing + the validation gate  (Task 10)
"""

from __future__ import annotations

__all__ = ["shards"]
