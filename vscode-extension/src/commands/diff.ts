/**
 * Httrace: Show API Drift
 *
 * Calls GET /v1/changes and displays results in a VS Code output channel.
 * Breaking changes are highlighted with ⚠ icons.
 */
import * as vscode from 'vscode';
import { HttptraceClient, EndpointChange } from '../api';

let _outputChannel: vscode.OutputChannel | undefined;

function getChannel(): vscode.OutputChannel {
  if (!_outputChannel) {
    _outputChannel = vscode.window.createOutputChannel('Httrace: API Drift');
  }
  return _outputChannel;
}

export async function showDiff(): Promise<void> {
  const config  = vscode.workspace.getConfiguration('httrace');
  const apiKey  = config.get<string>('apiKey', '').trim();
  const apiUrl  = config.get<string>('apiUrl', 'https://api.httrace.com');
  const service = config.get<string>('serviceName', '').trim();

  if (!apiKey || !service) {
    const action = await vscode.window.showWarningMessage(
      'Httrace: API key or service name not configured.',
      'Open Settings',
    );
    if (action === 'Open Settings') {
      vscode.commands.executeCommand('workbench.action.openSettings', 'httrace');
    }
    return;
  }

  const ch = getChannel();
  ch.clear();
  ch.show(true);

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `Httrace: Checking API drift for ${service}…`,
      cancellable: false,
    },
    async () => {
      try {
        const client = new HttptraceClient(apiKey, apiUrl);
        const data   = await client.getChanges(service);

        const changes   = data.changes || [];
        const untested  = data.untested_endpoints || [];
        const total     = changes.reduce((n, ep) => n + (ep.changes?.length || 0), 0) + untested.length;

        ch.appendLine(`Httrace API Drift — ${service}`);
        ch.appendLine(`${'─'.repeat(60)}`);
        ch.appendLine(`Checked: ${new Date().toLocaleString()}`);
        ch.appendLine('');

        if (!total) {
          ch.appendLine('✓  No schema drift detected. Tests are up to date.');
          vscode.window.showInformationMessage(`Httrace: No API drift for ${service}.`);
          return;
        }

        // Breaking changes
        const breaking = changes.flatMap(ep =>
          (ep.changes || [])
            .filter(c => c.type === 'breaking')
            .map(c => ({ endpoint: ep.endpoint, detail: c.detail }))
        );

        if (breaking.length) {
          ch.appendLine(`⚠  BREAKING CHANGES (${breaking.length})`);
          ch.appendLine('');
          for (const b of breaking) {
            ch.appendLine(`  ✗  ${b.endpoint}`);
            ch.appendLine(`     ${b.detail}`);
          }
          ch.appendLine('');
        }

        // Schema changes (non-breaking)
        const schema = changes.flatMap(ep =>
          (ep.changes || [])
            .filter(c => c.type !== 'breaking')
            .map(c => ({ endpoint: ep.endpoint, type: c.type, detail: c.detail }))
        );

        if (schema.length) {
          ch.appendLine(`~  Schema changes (${schema.length})`);
          ch.appendLine('');
          for (const s of schema) {
            ch.appendLine(`  ~  ${s.endpoint}  [${s.type}]`);
            if (s.detail) ch.appendLine(`     ${s.detail}`);
          }
          ch.appendLine('');
        }

        // Untested endpoints
        if (untested.length) {
          ch.appendLine(`+  New endpoints without tests (${untested.length})`);
          ch.appendLine('');
          for (const ep of untested) {
            ch.appendLine(`  +  ${ep}`);
          }
          ch.appendLine('');
        }

        ch.appendLine(`${'─'.repeat(60)}`);
        ch.appendLine(`${total} change(s) detected. Run "Httrace: Generate Tests" to update.`);

        const sev = breaking.length ? 'error' : 'warning';
        const msg = `Httrace: ${total} drift change${total !== 1 ? 's' : ''} detected for ${service}` +
                    (breaking.length ? ` — ${breaking.length} breaking` : '');
        if (sev === 'error') {
          vscode.window.showErrorMessage(msg, 'Generate Tests').then(a => {
            if (a === 'Generate Tests') {
              vscode.commands.executeCommand('httrace.generateTests');
            }
          });
        } else {
          vscode.window.showWarningMessage(msg, 'Generate Tests').then(a => {
            if (a === 'Generate Tests') {
              vscode.commands.executeCommand('httrace.generateTests');
            }
          });
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        ch.appendLine(`✗  Error: ${msg}`);
        vscode.window.showErrorMessage(`Httrace: API drift check failed — ${msg}`);
      }
    },
  );
}
