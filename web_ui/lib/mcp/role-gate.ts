import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';

type ToolCallback = (...args: unknown[]) => unknown;

/**
 * Require `allow(authInfo)` before every tool registered on `server` runs. Many tools read SQL directly,
 * so a valid token alone must not reach them; this applies the same local-role floor the EOS API guards use.
 */
export function gateTools(
  server: McpServer,
  allow: (authInfo: AuthInfo | undefined) => Promise<boolean>,
  deniedMessage: string
): void {
  const register = server.registerTool.bind(server) as (name: string, config: unknown, cb: ToolCallback) => unknown;
  server.registerTool = ((name: string, config: unknown, callback: ToolCallback) =>
    register(name, config, async (...args: unknown[]) => {
      // The SDK passes `extra` last: (args, extra) with an input schema, (extra) without
      const extra = args[args.length - 1] as { authInfo?: AuthInfo };
      if (!(await allow(extra.authInfo))) {
        return { content: [{ type: 'text', text: `Error: ${deniedMessage}` }], isError: true };
      }
      return callback(...args);
    })) as McpServer['registerTool'];
}
