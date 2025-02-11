"""Proctor the exec of bufrgruven and cobb.

This is my life, controlling PHP and Perl scripts.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import timedelta

import requests
from pyiem.util import exponential_backoff, logger, utc
from tqdm import tqdm

LOG = logger()
SERVICES = [
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com",
    "https://ftpprd.ncep.noaa.gov/data/nccf/com",
]
TEMPBASE = "/tmp"


def create_tempdirs(mybase, model, valid):
    """Generate our needed temp folders for running."""
    basedir = f"{mybase}/bufkit_{model}_{valid:%Y%m%d%H}"
    if not os.path.isdir(basedir):
        os.makedirs(basedir)
    for subdir in "ascii bufkit bufr cobb extracted gempak logs".split():
        newdir = os.path.join(basedir, subdir)
        if not os.path.isdir(newdir):
            os.makedirs(newdir)
    LOG.debug("Using %s has a temporary folder.", basedir)
    return basedir


def download_bufrsnd(tmpdir, model, valid, extra=""):
    """Get the file we need and save it."""
    # rap/prod/rap.20190814/rap.t00z.bufrsnd.tar.gz
    # gfs/prod/gfs.20190814/06/gfs.t06z.bufrsnd.tar.gz
    # nam/prod/nam.20190814/nam.t00z.tm00.bufrsnd.tar.gz
    model1 = model if model != "nam4km" else "nam"
    localfn = f"{tmpdir}/bufrsnd{extra}.tar.gz"
    attempt = 1
    dextra = "conus/" if model == "hrrr" else ""
    crazy = ""
    if extra == "hrrrak":
        model1 = "hrrr"
        dextra = "alaska/"
        crazy = ".ak"
        extra = ""
    while not os.path.isfile(localfn) and attempt < 60:
        # Flip/flop between the two services
        p1 = f"{valid:%H}/" if model == "gfs" else ""
        url = (
            f"{SERVICES[attempt % 2]}/{model1}/prod/{model1}.{valid:%Y%m%d}/"
            f"{p1}{'atmos/' if model1 == 'gfs' else ''}"
            f"{dextra}{model1}.t{valid:%H}z."
            f"{'tm00.' if model1 == 'nam' else ''}bufrsnd{extra}.tar{crazy}.gz"
        )
        LOG.info("attempt %s at fetching %s", attempt, url)
        # Fast fail on first attempt
        tmt = 5 if attempt < 3 else 60
        req = exponential_backoff(requests.get, url, timeout=tmt, stream=True)
        if req is None or req.status_code != 200:
            LOG.info(
                "download failed, sleeping 120s, response_code: %s",
                None if req is None else req.status_code,
            )
            # Fast iterate on the first attempt
            if attempt > 1:
                time.sleep(120)
        elif req and req.status_code == 200:
            with open(localfn, "wb") as fh:
                for chunk in req.iter_content(chunk_size=1024):
                    if chunk:
                        fh.write(chunk)
        attempt += 1

    LOG.info("Extracting %s", localfn)
    subprocess.call(f"tar -C {tmpdir}/extracted -xzf {localfn}", shell=True)


def load_stations(model: str):
    """Load up our station metadata and return xref."""
    stations = {}
    models = [model]
    if model == "nam4km":
        models.insert(0, "aknest")
    elif model == "hrrr":
        models.insert(0, "hrrrak")
    for _model in models:
        fn = f"bufrgruven/stations/{_model}_bufrstations.txt"
        with open(fn, encoding="ascii") as fh:
            for line in fh:
                tokens = line.split()
                if len(tokens) < 4:
                    continue
                stations[tokens[0]] = {
                    "icao": tokens[3].lower(),
                    "model": _model,
                }

    return stations


def run_bufrgruven(tmpdir, model, valid, sid, icao, modelsrc):
    """Run bufrgruven.pl please."""
    bufrfn = f"{tmpdir}/extracted/bufr.{sid}.{valid:%Y%m%d%H}"
    if not os.path.isfile(bufrfn):
        LOG.info("%s not found for bufrgruven", bufrfn)
        return False
    # remove an already existing bufr file, if it is there
    fn = f"{tmpdir}/bufr/{model}.{sid}.{valid:%Y%m%d%H}"
    if os.path.isfile(fn):
        os.unlink(fn)
    cmd = (
        f"perl bufrgruven/bufr_gruven.pl --dset {modelsrc} "
        f"--nfs {tmpdir}/extracted/bufr.STNM.YYYYMMDDCC "
        f"--date {valid:%Y%m%d} --cycle {valid:%H} --noascii "
        f"--metdat {tmpdir} "
        f"--stations {icao} --nozipit"
    )
    with subprocess.Popen(
        cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE
    ) as proc:
        out, err = proc.communicate()
        if proc.returncode != 0:
            logfn = f"{tmpdir}/logs/{icao}.log"
            with open(logfn, "w", encoding="ascii") as fh:
                fh.write(
                    f"Cmd: {cmd}\n"
                    f"Standard Out:\n{out.decode('ascii', 'ignore')}"
                    f"Standard Err:\n{err.decode('ascii', 'ignore')}"
                )
            return False
    # Remove GEMPAK Files as they seem to cause troubles if left laying around
    for suffix in ["sfc", "snd", "sfc_aux"]:
        fn = f"{tmpdir}/gempak/{valid:%Y%m%d%H}_{model}_bufr.{suffix}"
        if os.path.isfile(fn):
            os.unlink(fn)
    return True


def run_cobb(tmpdir, model, icao):
    """Run cobb.pl please."""
    bufrfn = f"{tmpdir}/bufkit/{model}_{icao}.buf"
    if not os.path.isfile(bufrfn):
        return
    cmd = f"perl cobb/cobb.pl {icao} {model} {tmpdir}/bufkit"
    proc = subprocess.Popen(
        cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE
    )
    out, err = proc.communicate()
    result = out.decode("ascii")
    if proc.returncode != 0 or len(result) < 1000:
        logfn = f"{tmpdir}/logs/cobb_{icao}.log"
        with open(logfn, "w", encoding="ascii") as fh:
            fh.write(
                f"Cmd: {cmd}\n"
                f"Standard Out:\n{out.decode('ascii', 'ignore')}"
                f"Standard Err:\n{err.decode('ascii', 'ignore')}"
            )
    else:
        cfn = f"{tmpdir}/cobb/{model}_{icao}.dat"
        with open(cfn, "w", encoding="ascii") as fh:
            fh.write(result)


def insert_ldm_bufkit(tmpdir, model, valid, icao, backfill):
    """Send this product away for LTS.

    Args:
      backfill (bool): If True, the LDM insert flags as archive only
    """
    filename = f"{tmpdir}/bufkit/{model}_{icao}.buf"
    if not os.path.isfile(filename):
        LOG.info("Filename %s missing, returning", filename)
        return
    p1 = "m" if valid.hour in [6, 18] and model in ["gfs", "nam"] else ""
    model1 = f"{model}{p1}"
    model2 = "gfs3" if model == "gfs" else model1
    # place a 'cache-buster' LDM product name on the end as we are inserting
    # with -i, so the product name is used to compute the MD5
    flag = "ac" if not backfill else "a"
    archivefn = get_archive_bufkit_filename(model, valid, icao)
    cmd = (
        f"pqinsert -i -p 'bufkit {flag} {valid:%Y%m%d%H%M} "
        f"bufkit/{model1}/{model2}_{icao}.buf bufkit/{archivefn} "
        f"bogus{utc():%Y%m%d%H%M%S}' {filename}"
    )
    with subprocess.Popen(
        cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE
    ) as proc:
        out, err = proc.communicate()
        if proc.returncode != 0:
            logfn = f"{tmpdir}/logs/ldmbufkit_{icao}.log"
            with open(logfn, "w", encoding="ascii") as fh:
                fh.write(
                    f"Standard Out:\n{out.decode('ascii', 'ignore')}"
                    f"Standard Err:\n{err.decode('ascii', 'ignore')}"
                )


def insert_ldm_cobb(tmpdir, model, valid, icao):
    """Send this product away for LTS."""
    filename = f"{tmpdir}/cobb/{model}_{icao}.dat"
    if not os.path.isfile(filename):
        return
    # place a 'cache-buster' LDM product name on the end as we are inserting
    # with -i, so the product name is used to compute the MD5
    model2 = "gfs3" if model == "gfs" else model
    cmd = (
        f"pqinsert -i -p 'bufkit c {valid:%Y%m%d%H%M} "
        f"cobb/{valid:%H}/{model}/{model2}_{icao}.dat "
        f"cobb/{valid:%H}/{model}/{model}_{icao}.dat "
        f"bogus{utc():%Y%m%d%H%M%S}' {filename}"
    )
    proc = subprocess.Popen(
        cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE
    )
    out, err = proc.communicate()
    if proc.returncode != 0:
        logfn = f"{tmpdir}/logs/ldmcobb_{icao}.log"
        with open(logfn, "w", encoding="ascii") as fh:
            fh.write(
                f"Standard Out:\n{out.decode('ascii', 'ignore')}"
                f"Standard Err:\n{err.decode('ascii', 'ignore')}"
            )


def delete_files(tmpdir, model, valid, sid, icao):
    """Need to cleanup after ourselves due to bufrgruven issues."""
    fn = f"{tmpdir}/bufkit/{model}_{icao}.buf"
    if os.path.isfile(fn):
        os.unlink(fn)
    fn = f"{tmpdir}/bufkit/{valid:%Y%m%d%H}.{model}_{icao}.buf"
    if os.path.isfile(fn):
        os.unlink(fn)
    fn = f"{tmpdir}/bufr/{model}.{sid}.{valid:%Y%m%d%H}"
    if os.path.isfile(fn):
        os.unlink(fn)


def rectify_cwd():
    """Make sure our CWD is okay."""
    mydir = os.sep.join([os.path.dirname(os.path.abspath(__file__)), "../"])
    LOG.info("Setting cwd to %s", mydir)
    os.chdir(mydir)


def workflow(args, model, valid, backfill):
    """Atomic workflow."""
    LOG.info(
        "Starting workflow model: %s valid: %s backfill: %s",
        model,
        valid,
        backfill,
    )
    # 1 Create metdat temp folder
    tmpdir = create_tempdirs(args.tmpdir, model, valid)
    # 2 Download files
    if model == "nam4km":
        download_bufrsnd(tmpdir, model, valid, "_conusnest")
        download_bufrsnd(tmpdir, model, valid, "_alaskanest")
    elif model == "hrrr":
        download_bufrsnd(tmpdir, model, valid)
        if valid.hour % 3 == 0:  # Only run every 3 hours
            download_bufrsnd(tmpdir, model, valid, "hrrrak")
    else:
        download_bufrsnd(tmpdir, model, valid)
    # 3 Load station dictionary
    stations = load_stations(model)
    # 4 Run bufrgruven and cobb
    progress = tqdm(stations.items(), disable=not sys.stdout.isatty())
    for sid, meta in progress:
        icao = meta["icao"]
        modelsrc = meta["model"]
        progress.set_description(f"{icao}[{modelsrc}]")
        if run_bufrgruven(tmpdir, model, valid, sid, icao, modelsrc):
            insert_ldm_bufkit(tmpdir, model, valid, icao, backfill)
            if model not in ["rap"]:
                run_cobb(tmpdir, model, icao)
                if not backfill:
                    insert_ldm_cobb(tmpdir, model, valid, icao)
            # Once we get > 1000 files in bufkit folder, bufrgruven bombs
            delete_files(tmpdir, model, valid, sid, icao)
    # 5. cleanup
    if not args.nocleanup:
        LOG.info("Blowing out tempdir: %s", tmpdir)
        subprocess.call(f"rm -rf {tmpdir}", shell=True)


def get_archive_bufkit_filename(model, valid, icao):
    """Our nomenclature."""
    # 06/nam/namm_kdsm.buf
    # 06/gfs/gfs3_kdsm.buf
    # 12/nam/nam_kdsm.buf
    # 12/nam4km/nam4km_kdsm.buf
    model1 = model
    if model == "nam" and valid.hour in [6, 18]:
        model1 = "namm"
    elif model == "gfs":
        model1 = "gfs3"
    return f"{valid:%H}/{model}/{model1}_{icao}.buf"


def main():
    """Our Main Method."""
    parser = argparse.ArgumentParser(description="Generate BufKit+Cobb Data.")
    parser.add_argument("model", help="model identifier to run this script for.")
    parser.add_argument("year", type=int, help="UTC Year")
    parser.add_argument("month", type=int, help="UTC Month")
    parser.add_argument("day", type=int, help="UTC Day")
    parser.add_argument("hour", type=int, help="UTC Hour")
    parser.add_argument(
        "--nocleanup",
        help="Leave temporary folder in-tact.",
        action="store_true",
    )
    parser.add_argument(
        "--backfill",
        help="Mark as a backfilling operation.",
        action="store_true",
    )
    parser.add_argument(
        "--tmpdir",
        default=TEMPBASE,
        help="Base directory to store temporary files.",
    )

    args = parser.parse_args()
    model = args.model
    valid = utc(args.year, args.month, args.day, args.hour)

    # 0 need to rectify cwd to be the base of the repo folder
    rectify_cwd()
    # Do work
    workflow(args, model, valid, args.backfill)
    # Check previous deltas to see if we need to reprocess
    for delta in [6, 12, 18, 24]:
        valid2 = valid - timedelta(hours=delta)
        testfn = valid2.strftime(
            "/isu/mtarchive/data/%Y/%m/%d/bufkit/"
        ) + get_archive_bufkit_filename(model, valid2, "kdsm")
        if not os.path.isfile(testfn):
            LOG.info("Rerunning %s due to missing %s", valid2, testfn)
            workflow(args, model, valid2, True)

    LOG.info("Done.")


if __name__ == "__main__":
    main()
