#!/usr/bin/env sh
# Flexible entrypoint: pick the MCP server or the CLI from the first argument.
#
#   docker run -i mempalace                  -> MCP server over stdio (default)
#   docker run -i mempalace mcp              -> MCP server over selected transport
#   docker run -p 8000:8000 mempalace http   -> MCP server over native HTTP
#   docker run mempalace cli search "query"  -> CLI passthrough (explicit)
#   docker run mempalace search "query"      -> CLI passthrough (implicit)
#
# `mcp` and `cli` are dispatch keywords; anything else is forwarded to the
# `mempalace` CLI verbatim so subcommands like `mine`, `search`, `wake-up`
# work without ceremony.
set -e

case "${1:-mcp}" in
    mcp)
        if [ "$#" -gt 0 ]; then
            shift
        fi
        exec mempalace-mcp "$@"
        ;;
    http)
        if [ "$#" -gt 0 ]; then
            shift
        fi
        exec mempalace-http-mcp "$@"
        ;;
    cli)
        if [ "$#" -gt 0 ]; then
            shift
        fi
        exec mempalace "$@"
        ;;
    *)
        exec mempalace "$@"
        ;;
esac
