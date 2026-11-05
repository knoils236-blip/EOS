"""ChemOS-side Python API and command-line client."""

import argparse
from pathlib import Path
import sys

from sila2.client import SilaClient

from .common import SCENE_ID, WORKFLOW, dumps, loads, validate_request, validate_session

ACCEPTED = frozenset({"starting", "running", "cancelling", "succeeded"})


class MatterixClient:
    def __init__(self, host="127.0.0.1", port=65200):
        self.client = SilaClient(host, port, insecure=True)

    def _observe(self, name, request, wait, on_event):
        feature = self.client.Matterix
        # 0.10.1's convenience command leaks an info thread on asynchronous disconnect.
        # Use its discovered SiLA stub directly so this client owns the stream lifetime.
        command = getattr(feature, name)._wrapped_command
        stub = feature._grpc_stub
        confirmation = getattr(stub, name)(command.parameters.to_message(dumps(request)))
        execution = confirmation.commandExecutionUUID
        stream = getattr(stub, name + "_Intermediate")(execution)
        try:
            for response in stream:
                state = loads(response.Event.value)
                if on_event is not None:
                    on_event(state)
                if not wait:
                    return state, execution
            return loads(getattr(stub, name + "_Result")(execution).Result.value), execution
        finally:
            stream.cancel()

    def submit(self, request, *, wait=False, on_event=None):
        request = validate_request(request)
        state, execution = self._observe("RunWorkflow", request, wait, on_event)
        if wait or state["status"] == "rejected":
            return state
        return {"request_id": request["request_id"], "execution_uuid": execution.value,
                "status": state["status"]}

    def capabilities(self):
        return loads(self.client.Matterix.GetCapabilities().Result)

    def create_session(self, request):
        request = validate_session(request)
        return loads(self.client.Matterix.CreateSession(dumps(request)).State)

    def get_session(self, identifier):
        return loads(self.client.Matterix.GetSession(identifier).State)

    def list_sessions(self):
        return loads(self.client.Matterix.ListSessions().State)

    def reset_session(self, identifier):
        return loads(self.client.Matterix.ResetSession(identifier).State)

    def end_session(self, identifier):
        return loads(self.client.Matterix.EndSession(identifier).State)

    def submit_task(self, request, *, wait=False, on_event=None):
        if not isinstance(request, dict):
            raise ValueError("Task request must be a JSON object")
        return self._observe("SubmitTask", request, wait, on_event)[0]

    def get(self, identifier):
        return loads(self.client.Matterix.GetRunState(identifier).State)

    def cancel(self, identifier):
        return loads(self.client.Matterix.CancelRun(identifier).State)

    def close(self):
        # SiLA 0.10.1 owns the channel; close subscriptions with it.
        self.client._channel.close()
        self.client._task_executor.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def read_job(path):
    """Read a ChemOS-style JSON job file; '-' reads standard input."""
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return loads(text)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=65200)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="list registered capabilities")
    create = commands.add_parser("create-session", help="start a persistent simulation session")
    create.add_argument("--session-id", required=True)
    create.add_argument("--experiment-id", required=True)
    create.add_argument("--scene-id", default=SCENE_ID)
    commands.add_parser("sessions", help="list local sessions")
    for name in ("session", "reset-session", "end-session"):
        commands.add_parser(name).add_argument("session_id")
    task = commands.add_parser("submit-task", help="submit a JSON job to a session")
    task.add_argument("--job", required=True, help="JSON job file, or - for standard input")
    task.add_argument("--wait", action="store_true")
    for name in ("status", "cancel"):
        commands.add_parser(name).add_argument("request_id")
    legacy = commands.add_parser("submit", help="legacy one-shot pickup_beaker run")
    legacy.add_argument("--request-id", required=True)
    legacy.add_argument("--sample-id")
    legacy.add_argument("--wait", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)

    def event(value):
        print(dumps(value), flush=True)

    with MatterixClient(args.host, args.port) as client:
        if args.command == "capabilities":
            state = client.capabilities()
        elif args.command == "create-session":
            state = client.create_session({"session_id": args.session_id,
                                           "experiment_id": args.experiment_id,
                                           "scene_id": args.scene_id})
        elif args.command == "sessions":
            state = client.list_sessions()
        elif args.command == "session":
            state = client.get_session(args.session_id)
        elif args.command == "reset-session":
            state = client.reset_session(args.session_id)
        elif args.command == "end-session":
            state = client.end_session(args.session_id)
        elif args.command == "submit-task":
            state = client.submit_task(read_job(args.job), wait=args.wait,
                                       on_event=event if args.wait else None)
        elif args.command == "submit":
            state = client.submit({"request_id": args.request_id, "workflow_id": WORKFLOW,
                                   "sample_id": args.sample_id}, wait=args.wait, on_event=event)
        elif args.command == "status":
            state = client.get(args.request_id)
        else:
            state = client.cancel(args.request_id)
    print(dumps(state), flush=True)
    if isinstance(state, list):
        return 0
    if args.command in {"submit-task", "submit"} and args.wait:
        return 0 if state["status"] == "succeeded" else 1
    if args.command in {"submit-task", "submit", "status", "cancel"}:
        return 0 if state["status"] in ACCEPTED else 1
    return 1 if state.get("status") in {"rejected", "interrupted"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
