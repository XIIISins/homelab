function vault-token --description 'Echo the Vault root token from 1Password'
    op item get 7g4grolyien2yqkm7me2jficmy --reveal --fields password
end
