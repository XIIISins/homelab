function ansible-vault-env --description "Source AppRole credentials for community.hashi_vault lookups"
    set -l env_file ~/.config/ansible/vault-approle.env
    if not test -f $env_file
        echo "Error: $env_file not found" >&2
        echo "Run AppRole setup first — see homelab-design.md AppRole bootstrap runbook." >&2
        return 1
    end

    set -l count 0
    # `or test -n "$line"` keeps a final line that lacks a trailing newline
    while read -l line; or test -n "$line"
        if string match -qr '^\s*$|^\s*#' -- $line
            continue
        end
        set line (string replace -r '^\s*export\s+' '' -- $line)
        if not string match -q '*=*' -- $line
            echo "Skipping line without '=': $line" >&2
            continue
        end
        set -l key (string trim -- (string split -m 1 -f1 = -- $line))
        set -l value (string split -m 1 -f2 = -- $line)
        if not string match -qr '^[A-Za-z_][A-Za-z0-9_]*$' -- $key
            echo "Skipping invalid variable name: $key" >&2
            continue
        end
        set -gx $key (string trim -c '"\'' -- "$value")
        set count (math $count + 1)
    end <$env_file

    echo "Loaded $count variables from $env_file"
end
