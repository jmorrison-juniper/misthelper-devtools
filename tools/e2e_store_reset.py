"""Clear the shared e2e run store and every site lock.

The end-to-end suite writes runs and locks to a real ArangoDB and a real
Redis. A failed walk can leave one live run and one held lock behind, and
both then refuse the next walk. This helper returns the shared store to the
clean state that a fresh checkout starts from. It touches the run collection
and the lock keys alone, so a seeded capture stays in place.

Run it from the repository root with the project interpreter:

    .venv\\Scripts\\python.exe tools\\e2e_store_reset.py
"""

from __future__ import annotations  # Keep the annotation style of the code base.

import logging  # The action log names each store step.

from src.upgrade_portal.capture import store  # The Arango accessor uses the portal config.
from src.upgrade_portal.runtime import lock  # The Redis accessor uses the portal config.

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")  # One line for each step.
logger = logging.getLogger("e2e_store_reset")  # A named log, so the output reads clearly.

RUN_COLLECTION = "upgrade_runs"  # The one collection that holds the live run refusal.
LOCK_PATTERN = "misthelper:lock:site:*"  # The key shape that contracts/site-lock.md fixes.


def clear_runs() -> None:
    """Remove every run, so no site reports a live run.

    Why:
        FR-037 refuses a new run while an unfinished run holds the site. A
        clean run collection frees every site for the next walk.
    """
    logger.info("reset: clear the run collection %s", RUN_COLLECTION)  # Before the write.
    database = store.connect_database()  # The portal config resolves the host and the account.
    database.collection(RUN_COLLECTION).truncate()  # One call empties the collection.
    logger.debug("reset: the run collection %s is empty", RUN_COLLECTION)  # After the write.


def clear_locks() -> None:
    """Delete every site lock key, so no site reports a holder.

    Why:
        A held lock refuses a second operator. A cleared key set frees every
        site, so the next walk starts a capture without a refusal.
    """
    logger.info("reset: clear the site lock keys that match %s", LOCK_PATTERN)  # Before the write.
    client = lock.connect_lock_store()  # The portal config resolves the Redis host and the port.
    keys = list(client.scan_iter(LOCK_PATTERN))  # The scan reads every site lock key at once.
    for key in keys:  # Each key is one held or expired site lock.
        client.delete(key)  # The delete frees that one site.
    logger.debug("reset: removed %d site lock keys", len(keys))  # After the write.


def main() -> None:
    """Clear the run collection and the lock keys in order."""
    clear_runs()  # The run refusal clears first.
    clear_locks()  # The lock refusal clears next.
    lock.reset_connection()  # Drop the cached client, so no live handle leaks past this run.


if __name__ == "__main__":  # The helper runs as a script, never as an import.
    main()  # One pass returns the shared store to the clean state.
