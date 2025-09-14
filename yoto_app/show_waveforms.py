import flet as ft
from loguru import logger
import numpy as np
import matplotlib.pyplot as plt
import io
import base64
import contextlib
import wave
import os
import tempfile

def show_waveforms_popup(page, file_rows_column, show_snack, gain_adjusted_files, audio_adjust_utils, waveform_cache):
    files = [getattr(row, 'filename', None) for row in file_rows_column.controls if getattr(row, 'filename', None)]
    if not files:
        show_snack("No files in upload queue.", error=True)
        return
    if not hasattr(page, '_track_gains'):
        page._track_gains = {}
    progress_text = ft.Text(f"Calculating waveform data... 0/{len(files)}", size=14)
    progress_bar = ft.ProgressBar(width=300, value=0)
    progress_dlg = ft.AlertDialog(
        title=ft.Text("Generating Waveforms"),
        content=ft.Column([
            progress_text,
            progress_bar
        ], expand=True),
        actions=[],
        modal=True
    )
    page.open(progress_dlg)
    page.update()

    def progress_callback(completed, total):
        progress_text.value = f"Calculating waveform data... {completed}/{total}"
        progress_bar.value = completed / total if total else 0
        page.update()

    from waveform_utils import batch_audio_stats
    stats_results = batch_audio_stats(files, waveform_cache, progress_callback=progress_callback)
    page.update()

    skipped_files = []
    for idx, stat in enumerate(stats_results):
        audio, max_amp, avg_amp, lufs, ext, filepath = stat
        if audio is None:
            reason = None
            if ext is None:
                reason = "Unrecognized or missing file extension."
            elif ext not in ['.wav', '.mp3']:
                reason = f"Unsupported extension: {ext}"
            elif not os.path.exists(filepath):
                reason = "File does not exist."
            else:
                reason = "Could not decode audio or file is empty/corrupt."
            skipped_files.append(f"{os.path.basename(filepath) or filepath}: {reason}")

    def plot_and_stats(audio, framerate, ext, filepath, gain_db=0.0):
        import pyloudnorm as pyln
        audio_adj = audio * (10 ** (gain_db / 20.0))
        max_amp = float(np.max(np.abs(audio_adj)))
        avg_amp = float(np.mean(np.abs(audio_adj)))
        try:
            meter = pyln.Meter(framerate)
            lufs = float(meter.integrated_loudness(audio_adj))
        except Exception:
            lufs = None
        max_points = 2000
        n = len(audio_adj)
        if n > max_points:
            idx = np.linspace(0, n - 1, max_points).astype(int)
            audio_plot = audio_adj[idx]
        else:
            audio_plot = audio_adj
        if ext == '.wav':
            with contextlib.closing(wave.open(filepath, 'rb')) as wf:
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                times = np.linspace(0, n_frames / framerate, num=n)
        else:
            framerate = 44100
            times = np.linspace(0, n / framerate, num=n)
        if n > max_points:
            times = times[idx]
        fig, ax = plt.subplots(figsize=(4, 1.2))
        ax.plot(times, audio_plot, color='blue')
        ax.set_title(os.path.basename(filepath), fontsize=8)
        ax.set_xlabel('Time (s)', fontsize=7)
        ax.set_ylabel('Amplitude', fontsize=7)
        ax.tick_params(axis='both', which='major', labelsize=6)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode('utf-8')
        fd, tmp_path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        with open(tmp_path, 'wb') as tmpfile:
            tmpfile.write(base64.b64decode(img_b64))
        lufs_str = f"LUFS: {lufs:.2f} dB" if lufs is not None else "LUFS: (unavailable)"
        label = ft.Text(f"Max amplitude: {max_amp:.2f}   Average amplitude: {avg_amp:.2f}   {lufs_str}", size=10, color=ft.Colors.BLUE)
        warning = None
        if lufs is not None:
            if lufs > -9:
                warning = ft.Text("Warning: LUFS is high! Track may be too loud for streaming (-9 dB or higher)", size=10, color=ft.Colors.RED)
            elif lufs > -16:
                warning = ft.Text("Warning: LUFS is moderately high (-16 dB to -9 dB)", size=10, color=ft.Colors.YELLOW_900)
        return label, warning, tmp_path

    per_track = []
    n_images = 0
    global_gain = {'value': getattr(page, '_global_gain', 0.0)}

    # Actually process stats_results to build per_track and n_images
    for stat in stats_results:
        audio, max_amp, avg_amp, lufs, ext, filepath = stat
        if audio is not None:
            if ext == '.wav':
                with contextlib.closing(wave.open(filepath, 'rb')) as wf:
                    framerate = wf.getframerate()
            else:
                framerate = 44100
            # Use last gain value for this file if available
            last_gain = page._track_gains.get(filepath, 0.0)
            gain_slider = ft.Slider(min=-20, max=20, divisions=40, value=last_gain, label="Gain: {value} dB", width=320)
            label, warning, tmp_path = plot_and_stats(audio, framerate, ext, filepath, gain_db=last_gain)
            img = ft.Image(src=tmp_path, width=320, height=100)
            col = ft.Column([])
            gain_val = {'value': last_gain}
            def on_gain_change(e, audio=audio, framerate=framerate, ext=ext, filepath=filepath, col=col, gain_val=gain_val):
                gain_db = e.control.value
                gain_val['value'] = gain_db
                page._track_gains[filepath] = gain_db
                label, warning, tmp_path = plot_and_stats(audio, framerate, ext, filepath, gain_db=gain_db)
                col.controls.clear()
                col.controls.append(label)
                if warning:
                    col.controls.append(warning)
                col.controls.append(ft.Image(src=tmp_path, width=320, height=100))
                # Show progress dialog only if saving gain-adjusted file (not for zero gain)
                show_progress = abs(gain_db) > 0.01
                progress_dlg = None
                if show_progress:
                    progress_dlg = ft.AlertDialog(title=ft.Text("Saving gain-adjusted audio..."), content=ft.ProgressBar(width=300), modal=True)
                    page.open(progress_dlg)
                    page.update()
                try:
                    if abs(gain_db) > 0.01:
                        if audio_adjust_utils is not None:
                            try:
                                temp_path = getattr(audio_adjust_utils, "save_adjusted_audio")(audio * (10 ** (gain_db / 20.0)), framerate, ext, filepath, gain_db)
                                gain_adjusted_files[filepath] = {'gain': gain_db, 'temp_path': temp_path}
                            except Exception as ex:
                                show_snack(f"Failed to save adjusted audio for upload: {ex}", error=True)
                    else:
                        gain_adjusted_files.pop(filepath, None)
                finally:
                    if progress_dlg:
                        page.close(progress_dlg)
                        page.update()
                page.update()
            gain_slider.on_change_end = on_gain_change
            def on_save_adjusted_audio_click(e, audio=audio, framerate=framerate, ext=ext, filepath=filepath, gain_val=gain_val):
                if audio_adjust_utils is None:
                    show_snack("audio_adjust_utils could not be loaded", error=True)
                    return
                progress_dlg = ft.AlertDialog(title=ft.Text("Saving gain-adjusted audio..."), content=ft.ProgressBar(width=300), modal=True)
                page.open(progress_dlg)
                page.update()
                try:
                    temp_path = getattr(audio_adjust_utils, "save_adjusted_audio")(audio * (10 ** (gain_val['value'] / 20.0)), framerate, ext, filepath, gain_val['value'])
                    show_snack(f"Saved adjusted audio to: {temp_path}")
                    if abs(gain_val['value']) > 0.01:
                        logger.debug(f"Storing gain-adjusted file for {filepath} with gain {gain_val['value']} dB at {temp_path}")
                        gain_adjusted_files[filepath] = {'gain': gain_val['value'], 'temp_path': temp_path}
                        # Update the upload queue row to use the new temp_path
                        for row in getattr(file_rows_column, 'controls', []):
                            logger.debug(f"Checking row with filename: {getattr(row, 'filename', None)} against {filepath}")
                            fileuploadrow = getattr(row, '_fileuploadrow', None)
                            if fileuploadrow and fileuploadrow.filepath == filepath:
                                fileuploadrow.update_file(temp_path)
                                # Also update the row's filename attribute for UI/lookup consistency
                                setattr(row, 'filename', temp_path)
                                break
                            else:
                                logger.debug("No matching fileuploadrow found in this row.")
                    else:
                        logger.debug(f"Gain is zero, removing any adjusted file entry for {filepath}")
                        gain_adjusted_files.pop(filepath, None)
                except Exception as ex:
                    show_snack(f"Failed to save adjusted audio: {ex}", error=True)
                finally:
                    page.close(progress_dlg)
                    page.update()
            save_btn = ft.TextButton("Save Adjusted Audio", on_click=on_save_adjusted_audio_click, tooltip="Save gain-adjusted audio to a temp file for upload")
            col.controls.append(label)
            if warning:
                col.controls.append(warning)
            col.controls.append(img)
            col.controls.append(save_btn)
            per_track.append((audio, framerate, ext, filepath, gain_slider, col, gain_val))
            n_images += 1
        else:
            per_track.append((None, None, None, None, None, ft.Text("(No waveform for file)", size=10, color=ft.Colors.RED), None))

    def on_global_gain_change(e):
        global_gain['value'] = e.control.value
        page._global_gain = e.control.value
        progress_text2 = ft.Text("Applying global gain to all tracks...", size=14)
        progress_bar2 = ft.ProgressBar(width=300, value=0)
        progress_dlg2 = ft.AlertDialog(title=ft.Text("Applying Global Gain..."), content=ft.Column([progress_text2, progress_bar2]), modal=True)
        page.open(progress_dlg2)
        page.update()
        total = len(per_track)
        completed = 0
        for i, (audio, framerate, ext, filepath, gain_slider, col, gain_val) in enumerate(per_track):
            if gain_slider is not None and audio is not None:
                gain_slider.value = global_gain['value']
                gain_val['value'] = global_gain['value']
                page._track_gains[filepath] = global_gain['value']
            completed += 1
            progress_text2.value = f"Processed {completed} of {total} tracks"
            progress_bar2.value = completed / total
            page.update()
        page.close(progress_dlg2)
        page.update()
        # Reopen the waveform popup after applying global gain, so waveforms are regenerated
        show_waveforms_popup(page, file_rows_column, show_snack, gain_adjusted_files, audio_adjust_utils, waveform_cache)

    global_gain_slider = ft.Slider(min=-20, max=20, divisions=40, value=global_gain['value'], label="Global Gain: {value} dB", width=320)
    global_gain_slider.on_change_end = on_global_gain_change

    save_btn = None
    if n_images > 0:
        def on_save_adjusted_audio_all_click(e):
            if audio_adjust_utils is None:
                show_snack("audio_adjust_utils could not be loaded", error=True)
                return
            progress_text3 = ft.Text("Saving gain-adjusted audio for all tracks...", size=14)
            progress_bar3 = ft.ProgressBar(width=300, value=0)
            progress_dlg3 = ft.AlertDialog(title=ft.Text("Saving gain-adjusted audio..."), content=ft.Column([progress_text3, progress_bar3]), modal=True)
            page.open(progress_dlg3)
            page.update()
            total = n_images
            completed = 0
            errors = []
            for audio, framerate, ext, filepath, gain_slider, col, gain_val in per_track:
                try:
                    temp_path = getattr(audio_adjust_utils, "save_adjusted_audio")(audio * (10 ** (gain_val['value'] / 20.0)), framerate, ext, filepath, gain_val['value'])
                    if abs(gain_val['value']) > 0.01:
                        gain_adjusted_files[filepath] = {'gain': gain_val['value'], 'temp_path': temp_path}
                        # Update the upload queue row to use the new temp_path
                        for row in getattr(file_rows_column, 'controls', []):
                            fileuploadrow = getattr(row, '_fileuploadrow', None)
                            if fileuploadrow and fileuploadrow.filepath == filepath:
                                fileuploadrow.update_file(temp_path)
                                setattr(row, 'filename', temp_path)
                                break
                    else:
                        gain_adjusted_files.pop(filepath, None)
                    progress_text3.value = f"Saved: {os.path.basename(filepath)}"
                except Exception as ex:
                    errors.append(f"{os.path.basename(filepath)}: {ex}")
                    progress_text3.value = f"Error: {os.path.basename(filepath)}"
                completed += 1
                progress_bar3.value = completed / total
                page.update()
            page.close(progress_dlg3)
            page.update()
            if errors:
                show_snack(f"Some files failed: {'; '.join(errors)}", error=True)
            else:
                show_snack("All gain-adjusted audio files saved and upload queue updated.")
        save_btn = ft.TextButton("Save Adjusted Audio", on_click=on_save_adjusted_audio_all_click, tooltip="Save gain-adjusted audio for all tracks in the dialog")

    images = []
    if n_images == 0:
        msg = "No waveforms could be generated for the files in the queue."
        if skipped_files:
            msg += "\n\nDetails:"
            for s in skipped_files:
                msg += f"\n- {s}"
        images = [ft.Text(msg, color=ft.Colors.RED)]
        dlg_actions = [ft.TextButton("Close", on_click=lambda e: page.close(dlg))]
    else:
        images.append(global_gain_slider)
        images.append(ft.Text("Adjust all tracks at once with the global gain slider above. You can still fine-tune individual tracks below.", size=10, color=ft.Colors.BLUE))
        for audio, framerate, ext, filepath, gain_slider, col, gain_val in per_track:
            if gain_slider is not None and audio is not None:
                images.append(ft.Column([
                    gain_slider,
                    col
                ]))
            else:
                images.append(col)
        images.insert(0, ft.Text(f"Generated {n_images} waveform(s) for {len(files)} file(s).", color=ft.Colors.GREEN))
        dlg_actions = [save_btn, ft.TextButton("Close", on_click=lambda e: page.close(dlg))] if save_btn else [ft.TextButton("Close", on_click=lambda e: page.close(dlg))]

    dlg = ft.AlertDialog(
        title=ft.Text("Waveforms for files to be uploaded"),
        content=ft.Column(images, scroll=ft.ScrollMode.AUTO, expand=True),
        actions=dlg_actions,
        scrollable=True
    )
    page.close(progress_dlg)
    page.open(dlg)
    page.update()
