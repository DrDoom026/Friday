"""Infrastructure shared by the PART 6 system tools.

The tools themselves live in :mod:`app.tools.system`. This package holds the
pieces they are built from, kept separate for the same reason :mod:`app.fs` is
separate from :mod:`app.tools.filesystem` - the tool is the interface, this is
the machinery, and the machinery is testable on its own.

- :mod:`app.system.errors`   - the error family every system tool raises.
- :mod:`app.system.command`  - running an external command without a shell.
- :mod:`app.system.audit`    - one audit record per system operation attempt.
- :mod:`app.system.procfs`   - reading process state straight out of ``/proc``.
- :mod:`app.system.docker_api` - the Docker Engine API over its unix socket.
"""
