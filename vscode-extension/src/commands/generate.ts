/**
 * Httrace: Generate Integration Tests
 *
 * Calls POST /v1/generate-tests, writes files to the configured output
 * directory inside the workspace, and opens the generated files.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { HttptraceClient } from '../api';

export async function generateTests(): Promise<void> {
  const config = vscode.workspace.getConfiguration('httrace');
  const apiKey      = config.get<string>('apiKey', '').trim();
  const apiUrl      = config.get<string>('apiUrl', 'https://api.httrace.com');
  const service     = config.get<string>('serviceName', '').trim();
  const format      = config.get<string>('testFormat', 'pytest');
  const outputDir   = config.get<string>('outputDirectory', 'tests/integration');

  if (!apiKey) {
    const action = await vscode.window.showWarningMessage(
      'Httrace: API key not configured.',
      'Open Settings',
    );
    if (action === 'Open Settings') {
      vscode.commands.executeCommand('workbench.action.openSettings', 'httrace.apiKey');
    }
    return;
  }

  if (!service) {
    const entered = await vscode.window.showInputBox({
      prompt: 'Enter the Httrace service name',
      placeHolder: 'my-api',
    });
    if (!entered) return;
    await config.update('serviceName', entered, vscode.ConfigurationTarget.Workspace);
  }

  const svcName = service || config.get<string>('serviceName', '');

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `Httrace: Generating ${format} tests for ${svcName}…`,
      cancellable: false,
    },
    async () => {
      try {
        const client = new HttptraceClient(apiKey, apiUrl);
        const data   = await client.generateTests(svcName, format);

        if (!data.generated) {
          vscode.window.showWarningMessage(
            `Httrace: No captures found for service '${svcName}'. ` +
            'Make sure the middleware is installed and receiving traffic.',
          );
          return;
        }

        // Write files to workspace
        const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!workspaceRoot) {
          vscode.window.showErrorMessage('Httrace: No workspace folder open.');
          return;
        }

        const outDir = path.join(workspaceRoot, outputDir);
        fs.mkdirSync(outDir, { recursive: true });

        const openFiles: string[] = [];
        for (const fileInfo of data.files) {
          const filename = path.basename(fileInfo.file);
          const code = data.code[fileInfo.file] || '';
          const filePath = path.join(outDir, filename);
          fs.writeFileSync(filePath, code, 'utf-8');
          openFiles.push(filePath);
        }

        // Open the first generated file
        if (openFiles.length > 0) {
          const doc = await vscode.workspace.openTextDocument(openFiles[0]);
          await vscode.window.showTextDocument(doc);
        }

        const qualifier = data.lang ? ` (${data.lang})` : '';
        vscode.window.showInformationMessage(
          `✓ Httrace: ${data.generated} test file${data.generated !== 1 ? 's' : ''} generated${qualifier} → ${outputDir}/`,
        );
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        vscode.window.showErrorMessage(`Httrace: Generate failed — ${msg}`);
      }
    },
  );
}
