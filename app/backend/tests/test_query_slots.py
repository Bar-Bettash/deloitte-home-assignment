import threading

import pytest
from app.query_slots import QuerySlots


def test_slots_bound_concurrent_admission_and_release():
    slots = QuerySlots()
    assert slots.try_acquire(2)
    assert slots.try_acquire(2)
    assert not slots.try_acquire(2)
    assert slots.active == 2
    slots.release()
    assert slots.try_acquire(2)
    assert slots.active == 2


def test_limit_is_read_per_call_so_a_lower_limit_takes_effect():
    slots = QuerySlots()
    assert slots.try_acquire(4)
    assert slots.try_acquire(4)
    assert not slots.try_acquire(1)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError):
        QuerySlots().try_acquire(limit)


def test_over_release_is_an_error():
    with pytest.raises(RuntimeError):
        QuerySlots().release()


def test_concurrent_acquire_never_exceeds_limit():
    slots = QuerySlots()
    barrier = threading.Barrier(32)
    admitted = []

    def worker():
        barrier.wait()
        admitted.append(slots.try_acquire(4))

    threads = [threading.Thread(target=worker) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(admitted) == 4
    assert slots.active == 4
