# Exam-like shell setup. Use:  source exam/setup-shell.sh   (add to ~/.bashrc to keep it)
alias k=kubectl
source <(kubectl completion bash) 2>/dev/null
complete -o default -F __start_kubectl k
export do="--dry-run=client -o yaml"     # k create deploy web --image=nginx $do > web.yaml
export now="--force --grace-period 0"    # k delete pod x $now
alias kn='kubectl config set-context --current --namespace'   # kn shopfleet
alias trainer="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/trainer"
[ -f ~/.vimrc ] || printf 'set expandtab\nset tabstop=2\nset shiftwidth=2\nset number\n' > ~/.vimrc
echo "exam shell ready: k, \$do, \$now, kn <ns>, trainer"
