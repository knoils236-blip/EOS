MCP Server
==========
EOS exposes an MCP server through the web UI. Connected assistants can inspect definitions,
submit work, monitor campaigns, and call device functions.

With :doc:`authentication` enabled, clients need a bearer token. The SQL tool requires the
superuser role. With authentication disabled, expose the endpoint only on trusted networks.

Connecting
----------

The MCP server is available at ``http://<web-ui-host>:3000/api/mcp`` using the Streamable HTTP transport.

Claude Code
~~~~~~~~~~~

.. code-block:: bash

    claude mcp add EOS http://localhost:3000/api/mcp --transport http

Claude Desktop
~~~~~~~~~~~~~~

Add to your ``claude_desktop_config.json``:

.. code-block:: json

    {
      "mcpServers": {
        "EOS": {
          "url": "http://localhost:3000/api/mcp",
          "transport": "streamable-http"
        }
      }
    }

Other MCP Clients
~~~~~~~~~~~~~~~~~

Point any MCP-compatible client to ``http://localhost:3000/api/mcp`` with the **Streamable HTTP** transport,
replacing ``localhost:3000`` with your web UI host and port if needed.


Capabilities
------------

Tools cover the following categories:

* **Campaigns, Protocol Runs, Tasks**: List, inspect, submit, and cancel at every level of the execution hierarchy.
* **Definitions**: Browse loaded task types, device types, lab layouts, and protocol workflows.
* **Management**: Load, unload, and reload labs, protocols, devices, and packages.
* **Optimizer**: Query optimizer state, update runtime parameters, and provide expert insights.
* **Devices**: List devices, inspect state, discover available RPC functions, and call them directly.
* **SQL**: Run read-only queries against the EOS database.
* **Filesystem**: Browse packages and read entity configuration files (YAML, Python).
* **Self-driving lab**: Read lab capabilities, save designed protocols, read campaign decision evidence,
  keep a lab notebook, and follow approval requests (see below).

Self-Driving Lab Loop
---------------------

These tools let an assistant act as the planner of a self-driving lab, from hypothesis to the next decision:

#. ``get_lab_capabilities``: what each lab can physically do. Scene operations carry their meaning, acceptance
   criteria, a ready-to-use task template and parameter ranges; scene notes state the initial state, the
   required order and single-use consumables.
#. ``add_notebook_entry`` / ``list_notebook_entries``: record hypotheses, design rationale, observations,
   interpretations and decisions, linked to a campaign or protocol run. People read them on the **AI 协作**
   page of the web UI. The notebook is separate from the optimizer journal, which BEACON feeds back into its
   own prompt.
#. ``validate_protocol_yaml`` and ``save_protocol``: validation runs the specs and each device's order check
   (for example, the Matterix pipetting station rejects skipped phases or reused TIPs). ``save_protocol``
   writes ``protocols/<type>/protocol.yml`` into a package, never overwriting unless asked, and loads the
   protocol when its labs are already loaded. It never loads labs or saves optimizer code.
#. ``submit_protocol_run`` / ``submit_campaign``: submissions whose labs are all marked ``simulated: true``
   in ``lab.yml`` (digital twins and mocks) start at once. Any other submission becomes an approval request
   that a person approves or rejects on the **AI 协作** page; ``list_approval_requests`` shows the outcome
   and the approver's note. MCP offers no approval tool. This is a workflow guardrail, not a security
   boundary: an assistant holding a token with the submitter role could call the REST API directly.
#. ``get_campaign_decision``: rounds, objectives, data sources and best values per source, to decide what to
   test next.


Examples
--------

Natural language prompts you might give an AI assistant connected to EOS via MCP:

.. code-block:: text

    "Show me the status of the catalyst_screening campaign and list all completed protocols"

    "Submit a color mixing protocol run targeting RGB(120, 45, 200)"

    "What devices are available in the wet lab? What functions does the liquid handler support?"

    "How did the yield change across the last 10 protocols in the solubility campaign?"

    "Reload the protocol definitions, I just updated the YAML for the pH optimization workflow"
