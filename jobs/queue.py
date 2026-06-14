from __future__ import annotations

from typing import List
from jobs.models import Job


class JobQueue:
    """
    Упрощённая очередь: выполняем синхронно, но храним статусы,
    чтобы пользователь видел “что происходит”.
    """
    def __init__(self) -> None:
        self.jobs: List[Job] = []

    def add(self, job: Job) -> None:
        self.jobs.insert(0, job)

    def run_next(self) -> None:
        if not self.jobs:
            return
        # берём первый queued
        for j in self.jobs:
            if j.status == "queued":
                self._run_job(j)
                return

    def _run_job(self, j: Job) -> None:
        try:
            j.status = "running"
            j.progress = "Выполнение..."
            res = j.run()
            j.result = res
            j.status = "done"
            j.progress = "Готово"
        except Exception as e:
            j.status = "error"
            j.error = str(e)
            j.progress = "Ошибка"
