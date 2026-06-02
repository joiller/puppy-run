from arq.connections import RedisSettings

from puppyrun_api.config import get_settings
from puppyrun_worker.jobs import run_dummy_agent_job, run_phase1_agent_job


def redis_settings_from_url(url: str) -> RedisSettings:
    if not url.startswith("redis://"):
        raise ValueError("PUPPYRUN_REDIS_URL must start with redis://")
    without_scheme = url.removeprefix("redis://")
    host_port, _, database = without_scheme.partition("/")
    host, _, port = host_port.partition(":")
    return RedisSettings(host=host, port=int(port or "6379"), database=int(database or "0"))


class WorkerSettings:
    functions = [run_dummy_agent_job, run_phase1_agent_job]
    redis_settings = redis_settings_from_url(get_settings().redis_url)
