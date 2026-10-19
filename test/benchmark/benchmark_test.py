import json
import matplotlib.pyplot as plt
import subprocess
import time

from IPython.core.pylabtools import figsize

FFMPEG_BUILD_PATH = '/home/smegmuss/Documents/CodeMaxxing/FFmpeg/ffmpeg-build-asm/'
FFMPEG_NOASM_BUILD_PATH = '/home/smegmuss/Documents/CodeMaxxing/FFmpeg/ffmpeg-build-noasm/'
EDGE264_PATH = '/home/smegmuss/Documents/CodeMaxxing/H264-learning/edge264/'

OUTPUT_FILE = 'benchmark_result.json'

targets = [
    {
        'name': 'undo264 scalar',
        'command' :'undo264 -i {0} --no-output --no-simd'
    },
    {
        'name': 'undo264 SSE',
        'command': 'undo264 -i {0} --no-output --no-avx2'
    },
    {
        'name': 'undo264 SSE + AVX2',
        'command': 'undo264 -i {0} --no-output'
    },
    {
        'name': 'FFmpeg NOASM',
        'command': FFMPEG_NOASM_BUILD_PATH + 'ffmpeg -threads 1 -i {0} -f null -'
    },
    {
        'name': 'FFmpeg ASM',
        'command': FFMPEG_BUILD_PATH + 'ffmpeg -threads 1 -i {0} -f null -'
    },
    {
        'name': 'edge264',
        'command': EDGE264_PATH + 'edge264_test {0} '
    }
]

bitstreams = [
    {
        'name': 'FH5 Baseline CAVLC',
        'path': 'videos/h264/horizon5-trailer/baseline/x264/slow.264',
        'nb_frames': 1483,
    },
    {
        'name': 'FH5 Main CABAC',
        'path': 'videos/h264/horizon5-trailer/main/x264/slow.264',
        'nb_frames': 1483,
    },
    {
        'name': 'FH5 High CABAC',
        'path': 'videos/h264/horizon5-trailer/high/x264/slow.264',
        'nb_frames': 1483,
    },
    {
        'name': 'FH5 High no deblock',
        'path': 'videos/h264/horizon5-trailer/high/x264/no-deblock.264',
        'nb_frames': 1483,
    },
    {
        'name': 'FH5 Baseline no deblock',
        'path': 'videos/h264/horizon5-trailer/baseline/x264/no-deblock.264',
        'nb_frames': 1483,
    }
]

if __name__ == '__main__':
    target_names = [target_name for target_name in [t['name'] for t in targets]]
    bitstream_names = [bts_name for bts_name in [b['name'] for b in bitstreams]]

    results = {target_name: {bitstream_name: {'secs': 0.0, 'fps': 0.0} for bitstream_name in bitstream_names} for target_name in target_names}

    for bitstream in bitstreams:
        b_name, b_path, nb_frames = bitstream.values()

        print(f'*** BISTREAM {b_name} ***\n')

        for target in targets:
            t_name, command = target.values()

            t0 = time.perf_counter()
            command = command.format(b_path)
            subprocess.run(command.format(b_path).split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elapsed_secs = round(time.perf_counter() - t0, 3)
            fps = round(nb_frames / elapsed_secs, 1)

            results[t_name][b_name]['secs'] = elapsed_secs
            results[t_name][b_name]['fps']  = fps

            print(f'   {t_name} took {elapsed_secs} seconds ({fps} fps)')

        print("\n\n")



    with open(OUTPUT_FILE, "w") as out:
        json.dump(results, out)

    print("wrote results to " + OUTPUT_FILE)




    # plot
    with open(OUTPUT_FILE, "r") as out:
        results = json.load(out)

    target_fps = {target: [b['fps'] for b in results[target].values()] for target in target_names}
    fig, ax = plt.subplots()

    res = ax.grouped_bar(target_fps, tick_labels=bitstream_names, group_spacing=1)
    for container in res.bar_containers:
        ax.bar_label(container, padding=8)

    ax.set_ylabel("Decoding speed in FPS")
    ax.set_title("Decoders decoding times per bitstream")
    ax.legend(loc='upper left', ncols=len(target_names))
    ax.set_ylim(0, max([max(target_fps[target]) for target in target_names]))


    plt.show()