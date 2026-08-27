#!/usr/bin/env python3
"""Paramiko SSH helper for Jetson Thor (192.168.20.66, user hkcrc1, pass=' ').

Usage:
  python _thor_ssh.py "command string"            # run command (UTF-8 output)
  python _thor_ssh.py -u <localfile> <remotefile> # upload file via SFTP
  python _thor_ssh.py -uc <localdir> <remotedir>  # upload whole dir (recursive)
"""
import os
import sys
import getpass
import io

import paramiko

HOST = "192.168.20.66"
USER = "hkcrc1"
PASS = " "


def connect(host=None, user=None, password=None):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host or HOST, 22, user or USER, password or PASS,
        timeout=15, banner_timeout=20, auth_timeout=20,
    )
    return c


def run(cmd, timeout=120):
    c = connect()
    try:
        stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
        out = stdout.read()
        err = stderr.read()
        rc = stdout.channel.recv_exit_status()
        sys.stdout.write(out.decode("utf-8", "replace"))
        sys.stderr.write(err.decode("utf-8", "replace"))
        return rc
    finally:
        c.close()


def _walk(local_dir):
    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for f in files:
            yield os.path.join(root, f)


def upload_dir(local, remote):
    c = connect()
    try:
        sftp = c.open_sftp()
        for lp in _walk(local):
            rp = os.path.join(remote, os.path.relpath(lp, local)).replace("\\", "/")
            rdir = os.path.dirname(rp)
            try:
                sftp.stat(rdir)
            except IOError:
                os.makedirs(rdir, exist_ok=True)
                # create remote dirs by mkdir one level at a time
                parts = rdir.split("/")
                cur = ""
                for p in parts:
                    if not p:
                        continue
                    cur = cur + "/" + p
                    try:
                        sftp.stat(cur)
                    except IOError:
                        try:
                            sftp.mkdir(cur)
                        except IOError:
                            pass
            sftp.put(lp, rp)
            print("UP", rp)
        sftp.close()
    finally:
        c.close()


def upload_file(local, remote):
    c = connect()
    try:
        sftp = c.open_sftp()
        rdir = os.path.dirname(remote)
        if rdir:
            parts = rdir.split("/")
            cur = ""
            for p in parts:
                if not p:
                    continue
                cur = cur + "/" + p
                try:
                    sftp.stat(cur)
                except IOError:
                    try:
                        sftp.mkdir(cur)
                    except IOError:
                        pass
        sftp.put(local, remote)
        print("UP", remote)
        sftp.close()
    finally:
        c.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "-u":
        upload_file(args[1], args[2])
    elif args and args[0] == "-uc":
        upload_dir(args[1], args[2])
    else:
        cmd = " ".join(args)
        sys.exit(run(cmd))
