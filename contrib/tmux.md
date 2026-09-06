# Running adsbtui under tmux or screen

adsbtui is an interactive curses program, so an SSH session that drops kills it along with
everything else in that terminal. Running it inside `tmux` or `screen` decouples the program's
life from any one SSH connection: you can detach, close your laptop, reconnect from somewhere
else, and reattach to find it exactly where you left it.

This is the practical alternative to the systemd kiosk unit (`adsb-tui-kiosk.service` in this
same directory) for a shared or remote box you SSH into rather than one with its own attached
display.

## tmux

Start a detachable session running adsbtui:

```bash
tmux new -s adsbtui adsbtui
```

Detach from it at any time with `Ctrl-b` then `d` — adsbtui keeps running. Reattach later, from
the same machine or a fresh SSH login:

```bash
tmux attach -t adsbtui
```

List running sessions (useful if you forgot the name, or want to check it's still alive after a
network blip):

```bash
tmux ls
```

Kill it for good (from outside the session):

```bash
tmux kill-session -t adsbtui
```

### Auto-start on login

Add this to `~/.bashrc` (or the equivalent for your shell) on the box you SSH into, to attach to
an existing adsbtui session or start one automatically every time you log in over SSH:

```bash
if [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ]; then
    tmux new-session -A -s adsbtui adsbtui
fi
```

`-A` attaches to the `adsbtui` session if it already exists, or creates it if not — so this is
safe to log in through multiple times without spawning duplicate sessions.

## screen

The same idea with `screen` instead of `tmux`, if that's what's already installed:

```bash
screen -S adsbtui adsbtui
```

Detach with `Ctrl-a` then `d`. Reattach with:

```bash
screen -r adsbtui
```

List sessions with `screen -ls`.

## Terminal resizing

Both tmux and screen forward `SIGWINCH` (the terminal-resize signal) to the program running
inside them, and adsbtui's curses event loop handles `KEY_RESIZE` by redrawing at the new size —
so resizing the tmux/screen window, or reattaching from a differently-sized terminal than the one
you detached from, works exactly as it would running adsbtui directly.
