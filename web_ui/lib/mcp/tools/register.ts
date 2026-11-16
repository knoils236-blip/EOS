import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { registerTaskTools } from './tasks';
import { registerProtocolTools } from './protocols';
import { registerCampaignTools } from './campaigns';
import { registerDefinitionTools } from './definitions';
import { registerManagementTools } from './management';
import { registerOptimizerTools } from './optimizer';
import { registerSqlTools } from './sql';
import { registerDeviceTools } from './devices';
import { registerFilesystemTools } from './filesystem';
import { registerSimulatorTools } from './simulator';
import { registerSdlTools } from './sdl';
import { callerHasRole } from '../auth';
import { gateTools } from '../role-gate';

export function registerAllTools(server: McpServer) {
  // Viewer is the floor; tools calling the EOS API are additionally checked there with the forwarded token
  gateTools(server, (authInfo) => callerHasRole(authInfo, 'VIEWER'), 'EOS tools require at least the viewer role');
  registerTaskTools(server);
  registerProtocolTools(server);
  registerCampaignTools(server);
  registerDefinitionTools(server);
  registerManagementTools(server);
  registerOptimizerTools(server);
  registerDeviceTools(server);
  registerSqlTools(server);
  registerFilesystemTools(server);
  registerSimulatorTools(server);
  registerSdlTools(server);
}
