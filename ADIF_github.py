#!/usr/bin/env python3

import os
import subprocess
import argparse
import sys

def run_command(cmd):
    """Execute a shell command and handle potential errors."""
    print(f"[RUNNING] {cmd}")
    result = subprocess.run(cmd, shell=True, executable="/bin/bash")
    if result.returncode != 0:
        print(f"[ERROR] Command failed with exit code {result.returncode}:\n{cmd}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Process VCF file for trio analysis and generate TSV.")
    parser.add_argument("-i", "--input", required=True, help="Input VCF file path")
    parser.add_argument("-o", "--output", required=True, help="Output TSV file path")
    parser.add_argument("--keep-temps", action="store_true", help="Keep intermediate temporary files")
    args = parser.parse_args()

    in_vcf = args.input
    out_tsv = args.output

    # Define intermediate filenames matching the original pipeline steps
    f_trio = "trio.vcf.gz"
    f_dp10 = "dp10.vcf.gz"
    f_gq20 = "dp10_GQ99.vcf.gz"
    f_auto = "dp10_GQ99_autosomeX.vcf.gz"
    f_ad = "dp10_GQ99_autosomeX_AD.vcf.gz"
    f_af = "dp10_GQ99_autosomeX_AD_AF.vcf.gz"
    f_af001 = "dp10_GQ99_autosomeX_AD_AF0.05.vcf.gz"
    f_af001_unzip = "dp10_GQ99_autosomeX_AD_AF0.05.vcf"
    f_coding = "dp10_GQ99_autosomeX_AD_AF0.05_coding.vcf"
    f_likely = "dp10_GQ99_autosomeX_AD_AF0.05_coding_likely.vcf.gz"

    # Step 1: Subset trio
    run_command(f"bcftools view -s 23_hEDS,27_KKY,24_hEDS -Oz -o {f_trio} {in_vcf}")
    run_command(f"bcftools index -t {f_trio}")

    # Step 2: Filter by Total Depth (INFO/DP > 10)
    run_command(f"bcftools view -i 'INFO/DP>10' {f_trio} -Oz -o {f_dp10}")
    run_command(f"bcftools index -t {f_dp10}")

    # Step 3: Filter by Genotype Quality (FMT/GQ >= 20)
    run_command(f"bcftools view -i 'FMT/GQ>=99' {f_dp10} -Oz -o {f_gq20}")

    # Step 4: Remove sex chromosomes and mtDNA
    run_command(f"bcftools view -t ^chrY,chrM {f_gq20} -Oz -o {f_auto}")

    # Step 5: Filter by Autosomal Dominant (AD) inheritance
    ad_cond = "'(GT[0]=\"0/1\" || GT[0]=\"1/1\") && GT[1]=\"0/0\" && (GT[2]=\"0/1\" || GT[2]=\"1/1\")'"
    run_command(f"bcftools view -i {ad_cond} {f_auto} -Oz -o {f_ad}")

    # Step 6: Extract MAX_AF using +split-vep plugin
    run_command(f"bcftools +split-vep {f_ad} -c MAX_AF -Oz -o {f_af}")
    run_command(f"tabix -p vcf {f_af}")

    # Step 7: Filter MAX_AF <= 0.001 or missing
    run_command(f"bcftools view -i 'INFO/MAX_AF<=0.05 || INFO/MAX_AF=\".\"' {f_af} -Oz -o {f_af001}")

    # Step 8: Decompress for awk processing
    run_command(f"gzip -d -f {f_af001}")

    # Step 9: Filter for extended coding variants via awk
    coding_terms = "missense_variant|frameshift_variant|stop_gained|stop_lost|start_lost|start_retained_variant|stop_retained_variant|splice_donor_variant|splice_acceptor_variant|splice_region_variant|inframe_deletion|inframe_insertion|protein_altering_variant|coding_sequence_variant"
    awk_cmd = f"awk -F'\\t' 'BEGIN{{OFS=\"\\t\"}} $0~/^#/ {{print; next}} $8~/({coding_terms})/ {{print}}' {f_af001_unzip} > {f_coding}"
    run_command(awk_cmd)

    # Step 10: Remove benign variants
    benign_cond = "'INFO/CSQ ~ \"\\\\|benign\\\\|\" || INFO/CSQ ~ \"\\\\|Benign\\\\|\"'"
    run_command(f"bcftools view -e {benign_cond} {f_coding} -Oz -o {f_likely}")

    # Step 11: Export to TSV format (Gene Location, Variant Info, GT, AD, DP)
    # Dynamically fetch sample names to ensure the TSV header perfectly aligns with the data
    sample_list_cmd = f"bcftools query -l {f_likely}"
    samples_output = subprocess.check_output(sample_list_cmd, shell=True, executable="/bin/bash").decode("utf-8").strip().split()
    
    header_cols = ["CHROM", "POS", "ID", "REF", "ALT"]
    for sample in samples_output:
        header_cols.extend([f"{sample}_GT", f"{sample}_AD", f"{sample}_DP"])
    header_str = "\t".join(header_cols) + "\n"

    with open(out_tsv, "w") as f:
        f.write(header_str)
        
    query_fmt = r"%CHROM\t%POS\t%ID\t%REF\t%ALT[\t%GT\t%AD\t%DP]\n"
    run_command(f"bcftools query -f '{query_fmt}' {f_likely} >> {out_tsv}")

    # Cleanup intermediate files unless specified otherwise
    if not args.keep_temps:
        print("[CLEANUP] Removing intermediate files...")
        temp_files = [
            f_trio, f"{f_trio}.tbi",
            f_dp10, f"{f_dp10}.tbi",
            f_gq20, f_auto, f_ad,
            f_af, f"{f_af}.tbi",
            f_af001_unzip, f_coding, f_likely
        ]
        for tmp in temp_files:
            if os.path.exists(tmp):
                os.remove(tmp)

    print(f"[SUCCESS] Pipeline complete. Final TSV generated at: {out_tsv}")

if __name__ == '__main__':
    main()