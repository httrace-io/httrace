/**
 * Tree data provider for the Httrace Coverage sidebar view.
 * Shows endpoints grouped by method with capture counts.
 */
import * as vscode from 'vscode';
import { CoverageEndpoint } from './api';

export class CoverageTreeProvider implements vscode.TreeDataProvider<CoverageTreeItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<CoverageTreeItem | undefined | null | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private _endpoints: CoverageEndpoint[] = [];
  private _service = '';

  update(service: string, endpoints: CoverageEndpoint[]): void {
    this._service   = service;
    this._endpoints = endpoints;
    this._onDidChangeTreeData.fire();
  }

  clear(): void {
    this._endpoints = [];
    this._service   = '';
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: CoverageTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(element?: CoverageTreeItem): CoverageTreeItem[] {
    if (!element) {
      // Root: group by METHOD
      if (!this._endpoints.length) {
        return [new CoverageTreeItem(
          'No coverage data — configure your API key',
          'info',
          vscode.TreeItemCollapsibleState.None,
        )];
      }

      const methods = [...new Set(this._endpoints.map(e => e.method))].sort();
      return methods.map(m => {
        const eps = this._endpoints.filter(e => e.method === m);
        const total = eps.reduce((s, e) => s + e.captures, 0);
        const item = new CoverageTreeItem(
          m,
          'method',
          vscode.TreeItemCollapsibleState.Expanded,
        );
        item.description = `${eps.length} endpoint${eps.length !== 1 ? 's' : ''} · ${total} captures`;
        item.methodGroup = m;
        return item;
      });
    }

    if (element.methodGroup) {
      return this._endpoints
        .filter(e => e.method === element.methodGroup)
        .sort((a, b) => a.path.localeCompare(b.path))
        .map(ep => {
          const label = ep.path;
          const item  = new CoverageTreeItem(
            label,
            ep.has_tests ? 'endpoint-tested' : 'endpoint-untested',
            vscode.TreeItemCollapsibleState.None,
          );
          item.description = `${ep.captures} captures · ${(ep.statuses || []).join(', ')}`;
          item.tooltip = `${ep.method} ${ep.path}\n${ep.captures} captures\n${ep.has_tests ? '✓ tests generated' : '○ no tests yet'}`;
          item.iconPath = new vscode.ThemeIcon(
            ep.has_tests ? 'pass' : 'circle-outline',
            ep.has_tests
              ? new vscode.ThemeColor('testing.iconPassed')
              : new vscode.ThemeColor('disabledForeground'),
          );
          return item;
        });
    }

    return [];
  }
}

export class CoverageTreeItem extends vscode.TreeItem {
  methodGroup?: string;

  constructor(
    label: string,
    public readonly kind: string,
    collapsibleState: vscode.TreeItemCollapsibleState,
  ) {
    super(label, collapsibleState);
    this.contextValue = kind;
  }
}
