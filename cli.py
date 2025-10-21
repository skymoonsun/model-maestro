#!/usr/bin/env python3
"""CLI tool for user management"""

import sys
import typer
from typing import Optional
from rich.console import Console
from rich.table import Table

from app.user_manager import user_manager

app = typer.Typer(help="Ollama Proxy API - User Management CLI")
console = Console()


@app.command("create-user")
def create_user(username: str):
    """
    Create a new user and generate JWT token
    
    Args:
        username: Username for the new user
    """
    user = user_manager.create_user(username)
    
    if user:
        console.print(f"[green]✓[/green] User created successfully!")
        console.print(f"\n[bold]Username:[/bold] {user.username}")
        console.print(f"[bold]Token:[/bold] {user.token}")
        console.print(f"[bold]Created:[/bold] {user.created_at}")
        console.print(f"\n[yellow]⚠ Save this token securely. You can refresh it later if needed.[/yellow]")
    else:
        console.print(f"[red]✗[/red] User '{username}' already exists!")
        sys.exit(1)


@app.command("delete-user")
def delete_user(username: str):
    """
    Delete a user
    
    Args:
        username: Username to delete
    """
    if user_manager.delete_user(username):
        console.print(f"[green]✓[/green] User '{username}' deleted successfully!")
    else:
        console.print(f"[red]✗[/red] User '{username}' not found!")
        sys.exit(1)


@app.command("refresh-token")
def refresh_token(username: str):
    """
    Refresh JWT token for a user
    
    Args:
        username: Username to refresh token for
    """
    user = user_manager.refresh_token(username)
    
    if user:
        console.print(f"[green]✓[/green] Token refreshed successfully!")
        console.print(f"\n[bold]Username:[/bold] {user.username}")
        console.print(f"[bold]New Token:[/bold] {user.token}")
        console.print(f"[bold]Updated:[/bold] {user.updated_at}")
        console.print(f"\n[yellow]⚠ Update your applications with the new token.[/yellow]")
    else:
        console.print(f"[red]✗[/red] User '{username}' not found!")
        sys.exit(1)


@app.command("show-user")
def show_user(username: str):
    """
    Show user information
    
    Args:
        username: Username to show
    """
    user = user_manager.get_user(username)
    
    if user:
        console.print(f"\n[bold cyan]User Information[/bold cyan]")
        console.print(f"[bold]Username:[/bold] {user.username}")
        console.print(f"[bold]Token:[/bold] {user.token}")
        console.print(f"[bold]Created:[/bold] {user.created_at}")
        if user.updated_at:
            console.print(f"[bold]Updated:[/bold] {user.updated_at}")
    else:
        console.print(f"[red]✗[/red] User '{username}' not found!")
        sys.exit(1)


@app.command("list-users")
def list_users():
    """
    List all users
    """
    users = user_manager.list_users()
    
    if not users:
        console.print("[yellow]No users found.[/yellow]")
        return
    
    table = Table(title="Users", show_header=True, header_style="bold magenta")
    table.add_column("Username", style="cyan")
    table.add_column("Token", style="green", overflow="fold")
    table.add_column("Created", style="yellow")
    table.add_column("Updated", style="yellow")
    
    for user in users:
        table.add_row(
            user.username,
            user.token[:20] + "..." if len(user.token) > 20 else user.token,
            user.created_at,
            user.updated_at or "-"
        )
    
    console.print(table)
    console.print(f"\n[bold]Total users:[/bold] {len(users)}")


if __name__ == "__main__":
    app()

