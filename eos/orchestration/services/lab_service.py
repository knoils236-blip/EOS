import traceback

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.entities.lab_def import LabDeviceDef
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.configuration.entities.task_def import DeviceAssignmentDef, TaskDef
from eos.devices.device_manager import DeviceManager
from eos.logging.logger import log
from eos.utils.di.di_container import inject


class LabService:
    """
    Top-level lab functionality integration.
    Exposes an interface for querying lab state.
    """

    @inject
    def __init__(
        self,
        configuration_manager: ConfigurationManager,
        device_manager: DeviceManager,
    ):
        self._configuration_manager = configuration_manager
        self._device_manager = device_manager

    async def get_editor_scenes(self) -> list[dict]:
        """Read registered scene presets without connecting to or starting equipment."""
        cm = self._configuration_manager
        scenes = []
        for lab_name, loaded in sorted(cm.get_loaded_labs().items()):
            lab = cm.package_manager.read_lab(lab_name)
            for device_name, device in lab.devices.items():
                device_class = cm.devices.plugin_types.get(device.type)
                if device_class is None:
                    continue
                scene = device_class.editor_presets(device.init_parameters)
                if scene is not None:
                    scenes.append({**scene, "lab_name": lab_name, "device_name": device_name, "loaded": loaded})
        return scenes

    def check_device_plans(self, protocol: ProtocolDef) -> list[dict[str, str]]:
        """
        Let each statically assigned device class check its ordered steps of the protocol offline.
        Returns [{"task", "message"}] for steps the device reports as certain to fail.
        """
        tasks = {task.name: task for task in protocol.tasks}

        def resolve(task: TaskDef, slot: str, seen: frozenset = frozenset()) -> tuple[str, str] | None:
            assignment = task.devices.get(slot)
            if isinstance(assignment, DeviceAssignmentDef):
                return assignment.lab_name, assignment.name
            if isinstance(assignment, str) and assignment not in seen:
                task_name, _, ref_slot = assignment.partition(".")
                if task_name in tasks:
                    return resolve(tasks[task_name], ref_slot, seen | {assignment})
            return None

        order = {name: index for index, name in enumerate(_topological_order(protocol.tasks))}
        ancestors = _ancestors(protocol.tasks)
        steps_by_device: dict[tuple[str, str], list[dict]] = {}
        for task in sorted(protocol.tasks, key=lambda t: order[t.name]):
            for slot in task.devices:
                device = resolve(task, slot)
                if device is None:
                    continue
                steps = steps_by_device.setdefault(device, [])
                ordered = not steps or steps[-1]["task"] in ancestors[task.name]
                steps.append({"task": task.name, "type": task.type, "parameters": task.parameters, "ordered": ordered})

        cm = self._configuration_manager
        errors = []
        for (lab_name, device_name), steps in steps_by_device.items():
            device = cm.package_manager.read_lab(lab_name).devices.get(device_name)
            device_class = cm.devices.plugin_types.get(device.type) if device else None
            if device_class is not None:
                errors.extend(device_class.check_plan(device.init_parameters, steps))
        return errors

    async def get_lab_devices(
        self, lab_types: set[str] | None = None, task_type: str | None = None
    ) -> dict[str, dict[str, LabDeviceDef]]:
        """Get the devices that are available in the given labs or for a specific task type."""
        use_all_labs = not lab_types or not any(lab_type.strip() for lab_type in lab_types)
        effective_lab_types = set(self._configuration_manager.labs.keys()) if use_all_labs else lab_types

        # Get device types for the specified task (if any)
        task_device_types = set()
        if task_type:
            task_spec = self._configuration_manager.task_specs.get_spec_by_type(task_type)
            task_device_types = {device.type for device in task_spec.devices.values()}

        lab_devices = {}
        for lab_type in effective_lab_types:
            lab = self._configuration_manager.labs.get(lab_type)
            if not lab:
                continue

            filtered_devices = {
                name: device
                for name, device in lab.devices.items()
                if not task_device_types or device.type in task_device_types
            }

            if filtered_devices:
                lab_devices[lab_type] = filtered_devices

        return lab_devices

    async def get_device_report(self, lab_name: str, device_name: str) -> dict:
        """Get a report for a specific device."""
        try:
            device_actor = self._device_manager.get_device_actor(lab_name, device_name)
            return await device_actor.report.remote()
        except Exception:
            log.error(
                f"Failed to get device report for lab '{lab_name}' and device '{device_name}': {traceback.format_exc()}"
            )
            raise

    async def call_device_function(self, lab_id: str, device_id: str, function_name: str, parameters: dict) -> dict:
        """Call any function on a device actor with the given parameters."""
        try:
            device_actor = self._device_manager.get_device_actor(lab_id, device_id)

            # Get the function from the actor
            if not hasattr(device_actor, function_name):
                raise AttributeError(f"Device '{lab_id}.{device_id}' does not have function '{function_name}'")

            device_function = getattr(device_actor, function_name)

            # Call the function with parameters
            if parameters:
                return await device_function.remote(**parameters)
            return await device_function.remote()
        except Exception:
            log.error(
                f"Failed to call function '{function_name}' on device '{lab_id}.{device_id}': {traceback.format_exc()}"
            )
            raise


def _topological_order(tasks: list[TaskDef]) -> list[str]:
    """Task names in dependency order, keeping the file order among tasks that are ready together."""
    remaining = {task.name: set(task.dependencies) for task in tasks}
    order = []
    while remaining:
        ready = [name for name, deps in remaining.items() if not deps - set(order)]
        if not ready:  # a cycle, reported by the protocol validator
            ready = list(remaining)
        order.extend(ready[:1])
        del remaining[ready[0]]
    return order


def _ancestors(tasks: list[TaskDef]) -> dict[str, set[str]]:
    dependencies = {task.name: set(task.dependencies) for task in tasks}
    result: dict[str, set[str]] = {}

    def visit(name: str) -> set[str]:
        if name not in result:
            result[name] = set()
            for dependency in dependencies.get(name, ()):
                result[name] |= {dependency} | visit(dependency)
        return result[name]

    for name in dependencies:
        visit(name)
    return result
