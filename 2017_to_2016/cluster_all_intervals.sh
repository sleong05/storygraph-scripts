D=$(sbatch --array=1-108 cluster_2017_to_2026.sh days | awk '{print $4}')
W=$(sbatch --dependency=afterok:$D cluster_2017_to_2026.sh weeks | awk '{print $4}')
M=$(sbatch --dependency=afterok:$W cluster_2017_to_2026.sh months | awk '{print $4}')
Y=$(sbatch --dependency=afterok:$M --mem=96G cluster_2017_to_2026.sh years | awk '{print $4}')

echo "Submitted: days=$D weeks=$W months=$M years=$Y"

