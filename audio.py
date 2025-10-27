"""
Audio processing module for MultiSOCIAL Toolbox.

This module provides functionality for:
- Audio feature extraction using OpenSMILE
- Speech transcription using Whisper
- Speaker diarization using PyAnnote
- Speaker-transcript alignment
"""

import os
import torch
import librosa
import opensmile
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
from pyannote.audio import Pipeline


class AudioProcessor:
    def __init__(self, output_audio_features_folder, output_transcripts_folder, status_callback=None, enable_speaker_diarization=True, auth_token=None):
        """
        Initialize the AudioProcessor.
        
        Args:
            output_audio_features_folder (str): Path to folder for saving audio features CSV files
            output_transcripts_folder (str): Path to folder for saving transcript text files
            status_callback (callable, optional): Callback function for status updates
            enable_speaker_diarization (bool): Whether to enable speaker diarization for transcripts
            auth_token (str, optional): Hugging Face auth token for pyannote (required for speaker diarization)
        """
        self.output_audio_features_folder = output_audio_features_folder
        self.output_transcripts_folder = output_transcripts_folder
        self.status_callback = status_callback
        self.enable_speaker_diarization = enable_speaker_diarization
        self.auth_token = auth_token
        
        # Ensure output directories exist (only if they are not None)
        if self.output_audio_features_folder is not None:
            os.makedirs(self.output_audio_features_folder, exist_ok=True)
        if self.output_transcripts_folder is not None:
            os.makedirs(self.output_transcripts_folder, exist_ok=True)
        
        # Initialize device for Whisper
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        
        # Whisper model will be loaded lazily when needed
        self.whisper_model = None
        self.whisper_processor = None
        self.whisper_pipe = None
        
        # Speaker diarizer will be loaded lazily when needed
        self.speaker_diarizer = None

    def set_status_message(self, message):
        """Safely update status message using callback if available."""
        if self.status_callback:
            self.status_callback(message)

    def extract_audio_features(self, filepath, progress_callback=None):
        """
        Extract audio features from a single WAV file using OpenSMILE.
        
        Args:
            filepath (str): Path to the WAV file
            progress_callback (callable, optional): Callback function for progress updates
            
        Returns:
            str: Path to the saved CSV file with features
        """
        if self.output_audio_features_folder is None:
            raise ValueError("Audio features output folder not configured")
            
        try:
            if progress_callback:
                progress_callback(0)
            
            # Configure OpenSMILE feature extraction
            feature_set_name = opensmile.FeatureSet.ComParE_2016
            feature_level_name = opensmile.FeatureLevel.LowLevelDescriptors

            if progress_callback:
                progress_callback(10)
            
            # Initialize OpenSMILE processor
            smile = opensmile.Smile(feature_set=feature_set_name, feature_level=feature_level_name)
            
            if progress_callback:
                progress_callback(20)
            
            # Load audio file using librosa
            y, sr = librosa.load(filepath)
            
            if progress_callback:
                progress_callback(40)
            
            # Extract features using OpenSMILE
            features = smile.process_signal(y, sr)
            
            if progress_callback:
                progress_callback(70)

            # Add timestamp columns as the leftmost columns
            # Calculate precise frame duration based on actual audio length
            audio_duration = len(y) / sr  # Total audio duration in seconds
            num_frames = len(features)
            frame_duration = audio_duration / num_frames  # Actual frame duration
            
            # Create timestamps for each frame
            timestamps_seconds = [i * frame_duration for i in range(num_frames)]
            timestamps_milliseconds = [t * 1000 for t in timestamps_seconds]  # Convert to milliseconds
            timestamps_formatted = [f"{int(t//60):02d}:{t%60:06.3f}" for t in timestamps_seconds]  # MM:SS.mmm format
            
            # Insert timestamp columns at the beginning
            features.insert(0, 'Timestamp_Seconds', timestamps_seconds)
            features.insert(1, 'Timestamp_Milliseconds', timestamps_milliseconds)
            features.insert(2, 'Timestamp_Formatted', timestamps_formatted)
            
            # Save features to CSV with original OpenSMILE column names and timestamps
            output_csv = os.path.join(
                self.output_audio_features_folder, 
                os.path.splitext(os.path.basename(filepath))[0] + ".csv"
            )
            features.to_csv(output_csv, index=False)
            
            if progress_callback:
                progress_callback(100)

            print(f"Saved audio features: {output_csv}")
            return output_csv

        except Exception as e:
            error_msg = f'Error extracting audio features from {filepath}: {e}'
            print(error_msg)
            raise Exception(error_msg)

    def extract_audio_features_batch(self, audio_files, progress_callback=None):
        """
        Batch process multiple audio files to extract features.
        
        Args:
            audio_files (list): List of paths to WAV files
            progress_callback (callable, optional): Callback function for progress updates
        """
        total_files = len(audio_files)
        
        for i, audio_file in enumerate(audio_files):
            self.set_status_message(f"🎧 Extracting audio features from: {os.path.basename(audio_file)}")
            print(f"Extracting features from: {audio_file}")
            
            # Create progress callback for this audio file
            def make_progress_callback(audio_index, total_audios):
                def file_progress_callback(extraction_progress):
                    if progress_callback:
                        # Calculate overall progress: (audio_index-1)/total_audios + extraction_progress/total_audios
                        overall_progress = int(((audio_index - 1) / total_audios) * 100 + (extraction_progress / total_audios))
                        progress_callback(overall_progress)
                return file_progress_callback
            
            try:
                self.extract_audio_features(audio_file, progress_callback=make_progress_callback(i + 1, total_files))
            except Exception as e:
                print(f"Error processing {audio_file}: {e}")
                continue

    def _load_whisper_model(self, progress_callback=None):
        """
        Load Whisper model and processor (lazy loading).
        
        Args:
            progress_callback (callable, optional): Callback function for progress updates
        """
        if self.whisper_model is not None:
            return  # Already loaded
            
        if progress_callback:
            progress_callback(5)

        model_id = "distil-whisper/distil-large-v3"

        if progress_callback:
            progress_callback(10)

        # Load model
        self.whisper_model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id, 
            torch_dtype=self.torch_dtype, 
            low_cpu_mem_usage=True, 
            use_safetensors=True
        )
        
        if progress_callback:
            progress_callback(25)
            
        self.whisper_model.to(self.device)

        if progress_callback:
            progress_callback(40)

        # Load processor
        self.whisper_processor = AutoProcessor.from_pretrained(model_id)

        if progress_callback:
            progress_callback(55)

        # Create pipeline with explicit configuration to avoid warnings
        self.whisper_pipe = pipeline(
            "automatic-speech-recognition",
            model=self.whisper_model,
            tokenizer=self.whisper_processor.tokenizer,
            feature_extractor=self.whisper_processor.feature_extractor,
            max_new_tokens=128,
            chunk_length_s=25,
            batch_size=16,
            torch_dtype=self.torch_dtype,
            device=self.device,
            # Add return_timestamps for better processing
            return_timestamps=True
        )

        if progress_callback:
            progress_callback(70)

    def _load_speaker_diarizer(self, progress_callback=None):
        """Load the PyAnnote speaker diarization model (lazy loading)"""
        if self.speaker_diarizer is not None:
            return
            
        try:
            if progress_callback:
                progress_callback(5)
                
            print("Loading PyAnnote speaker diarization model...")
            # Use PyAnnote diarization
            self.speaker_diarizer = PyAnnoteSpeakerDiarizer(progress_callback, self.auth_token)
            
            if progress_callback:
                progress_callback(15)
                
        except Exception as e:
            raise Exception(f"Failed to load PyAnnote speaker diarization model: {str(e)}")
    
    def preload_speaker_diarizer(self):
        """Pre-load the speaker diarization model to avoid delays during first use"""
        try:
            print("Pre-loading PyAnnote speaker diarization model...")
            self._load_speaker_diarizer()
            print("✓ PyAnnote model pre-loaded successfully")
        except Exception as e:
            print(f"Warning: Could not pre-load PyAnnote model: {e}")

    def extract_transcript(self, filepath, progress_callback=None):
        """
        Extract transcript from a single WAV file using Whisper with optional speaker diarization.
        
        Args:
            filepath (str): Path to the WAV file
            progress_callback (callable, optional): Callback function for progress updates
            
        Returns:
            str: Path to the saved transcript file
        """
        if self.output_transcripts_folder is None:
            raise ValueError("Transcripts output folder not configured")
            
        try:
            if progress_callback:
                progress_callback(0)
            
            print(f"Loading Whisper model for {filepath}...")
            
            # Load Whisper model (lazy loading)
            self._load_whisper_model(progress_callback)

            print(f"Transcribing {filepath}...")
            
            # Transcribe audio with timestamps
            result = self.whisper_pipe(filepath)
            transcript = result['text']
            
            # Store the full result for timestamped segments
            self.whisper_result = result

            if progress_callback:
                progress_callback(50)

            # Perform speaker diarization if enabled
            speaker_segments = None
            if self.enable_speaker_diarization:
                try:
                    print(f"Performing speaker diarization for {filepath}...")
                    self._load_speaker_diarizer(progress_callback)
                    speaker_segments = self.speaker_diarizer.diarize_speakers(filepath, progress_callback)
                    print(f"Found {len(speaker_segments)} speaker segments")
                except Exception as e:
                    print(f"Speaker diarization failed: {str(e)}")
                    print("Continuing with transcript only...")
                    speaker_segments = None

            if progress_callback:
                progress_callback(90)

            # Format transcript with speaker labels if available
            if speaker_segments and len(speaker_segments) > 0:
                formatted_transcript = self._format_transcript_with_speakers(transcript, speaker_segments)
            else:
                formatted_transcript = transcript

            # Save transcript to text file
            output_txt = os.path.join(
                self.output_transcripts_folder, 
                os.path.splitext(os.path.basename(filepath))[0] + ".txt"
            )

            with open(output_txt, 'w') as f:
                f.write(formatted_transcript)

            if progress_callback:
                progress_callback(100)

            print(f"Saved transcript: {output_txt}")
            return output_txt

        except Exception as e:
            error_msg = f'Error transcribing {filepath}: {e}'
            print(error_msg)
            raise Exception(error_msg)

    def _format_transcript_with_speakers(self, transcript, speaker_segments):
        """
        Format transcript with speaker labels based on speaker segments.
        
        Args:
            transcript (str): Raw transcript from Whisper
            speaker_segments (list): List of speaker segments with timestamps
            
        Returns:
            str: Formatted transcript with speaker labels
        """
        if not speaker_segments:
            return transcript
            
        # Create a formatted transcript with speaker labels
        formatted_lines = []
        formatted_lines.append("=== TRANSCRIPT WITH SPEAKER LABELS ===\n")
        
        # Add speaker segments overview
        formatted_lines.append("Speaker Segments:")
        for segment in speaker_segments:
            start_time = f"{int(segment['start']//60):02d}:{segment['start']%60:06.3f}"
            end_time = f"{int(segment['end']//60):02d}:{segment['end']%60:06.3f}"
            formatted_lines.append(f"  {segment['speaker']}: {start_time} --> {end_time}")
        
        formatted_lines.append("\n=== ALIGNED TRANSCRIPT ===\n")
        
        # Get Whisper's timestamped segments for alignment
        try:
            # Extract timestamped segments from Whisper result
            whisper_segments = self._extract_whisper_segments(transcript)
            
            if whisper_segments:
                # Align Whisper segments with speaker segments
                aligned_transcript = self._align_segments_with_speakers(whisper_segments, speaker_segments)
                formatted_lines.append(aligned_transcript)
            else:
                # Fallback to raw transcript if no timestamps available
                formatted_lines.append("Raw Transcript:")
                formatted_lines.append(transcript)
                
        except Exception as e:
            print(f"Warning: Could not align transcript with speakers: {e}")
            formatted_lines.append("Raw Transcript:")
            formatted_lines.append(transcript)
        
        return "\n".join(formatted_lines)
    
    def _extract_whisper_segments(self, transcript):
        """
        Extract timestamped segments from Whisper result.
        
        Args:
            transcript (str): Raw transcript from Whisper
            
        Returns:
            list: List of (start_time, end_time, text) tuples
        """
        # Use the stored Whisper result if available
        if hasattr(self, 'whisper_result') and self.whisper_result:
            result = self.whisper_result
            
            # Check if we have chunked output with timestamps
            if 'chunks' in result and result['chunks']:
                segments = []
                for chunk in result['chunks']:
                    if 'timestamp' in chunk and chunk['timestamp'] is not None:
                        start_time = chunk['timestamp'][0] if chunk['timestamp'][0] is not None else 0.0
                        end_time = chunk['timestamp'][1] if chunk['timestamp'][1] is not None else start_time + 1.0
                        text = chunk['text'].strip()
                        
                        if text:
                            segments.append({
                                'start': start_time,
                                'end': end_time,
                                'text': text
                            })
                
                if segments:  # Only return if we found valid segments
                    return segments
        
        # Fallback: create approximation from transcript
        sentences = transcript.split('. ')
        segments = []
        current_time = 0.0
        
        for sentence in sentences:
            if sentence.strip():
                # Estimate duration based on text length (rough approximation)
                duration = max(1.0, len(sentence) * 0.1)  # ~0.1 seconds per character
                end_time = current_time + duration
                
                segments.append({
                    'start': current_time,
                    'end': end_time,
                    'text': sentence.strip() + ('.' if not sentence.endswith('.') else '')
                })
                current_time = end_time
        
        return segments
    
    def _align_segments_with_speakers(self, whisper_segments, speaker_segments):
        """
        Align Whisper transcript segments with speaker segments.
        
        Args:
            whisper_segments (list): List of Whisper segments with timestamps
            speaker_segments (list): List of speaker segments with timestamps
            
        Returns:
            str: Aligned transcript with speaker labels
        """
        aligned_lines = []
        
        for whisper_seg in whisper_segments:
            # Find which speaker was talking during this time segment
            speaker = self._find_speaker_for_time(
                whisper_seg['start'], 
                whisper_seg['end'], 
                speaker_segments
            )
            
            # Format the line with speaker label
            start_time = f"{int(whisper_seg['start']//60):02d}:{whisper_seg['start']%60:06.3f}"
            end_time = f"{int(whisper_seg['end']//60):02d}:{whisper_seg['end']%60:06.3f}"
            
            aligned_lines.append(f"{speaker}: [{start_time} - {end_time}] {whisper_seg['text']}")
        
        return "\n".join(aligned_lines)
    
    def _find_speaker_for_time(self, start_time, end_time, speaker_segments):
        """
        Find which speaker was talking during a given time period.
        
        Args:
            start_time (float): Start time of the segment
            end_time (float): End time of the segment
            speaker_segments (list): List of speaker segments
            
        Returns:
            str: Speaker label for the time period
        """
        if not speaker_segments:
            return "UNKNOWN"
            
        # Find the speaker segment that overlaps most with the given time period
        best_speaker = "UNKNOWN"
        best_overlap = 0
        
        for seg in speaker_segments:
            try:
                # Calculate overlap between the time periods
                overlap_start = max(start_time, seg['start'])
                overlap_end = min(end_time, seg['end'])
                overlap_duration = max(0, overlap_end - overlap_start)
                
                if overlap_duration > best_overlap:
                    best_overlap = overlap_duration
                    best_speaker = seg['speaker']
            except (KeyError, TypeError) as e:
                print(f"Warning: Invalid speaker segment format: {e}")
                continue
        
        return best_speaker

    def extract_transcripts_batch(self, audio_files, progress_callback=None):
        """
        Batch process multiple audio files to generate transcripts.
        
        Args:
            audio_files (list): List of paths to WAV files
            progress_callback (callable, optional): Callback function for progress updates
        """
        total_files = len(audio_files)

        for i, audio_file in enumerate(audio_files):
            self.set_status_message(f"🗣️ Transcribing: {os.path.basename(audio_file)}")
            
            # Create progress callback for this audio file
            def make_progress_callback(audio_index, total_audios):
                def file_progress_callback(transcription_progress):
                    if progress_callback:
                        # Calculate overall progress: (audio_index-1)/total_audios + transcription_progress/total_audios
                        overall_progress = int(((audio_index - 1) / total_audios) * 100 + (transcription_progress / total_audios))
                        progress_callback(overall_progress)
                return file_progress_callback
        
            try:
                self.extract_transcript(audio_file, progress_callback=make_progress_callback(i + 1, total_files))
            except Exception as e:
                print(f"Error processing {audio_file}: {e}")
                continue

    def process_audio_file(self, filepath, extract_features=True, extract_transcript=True, progress_callback=None):
        """
        Process a single audio file for both features and transcript.
        
        Args:
            filepath (str): Path to the WAV file
            extract_features (bool): Whether to extract audio features
            extract_transcript (bool): Whether to extract transcript
            progress_callback (callable, optional): Callback function for progress updates
            
        Returns:
            dict: Dictionary with paths to saved files
        """
        results = {}
        
        if extract_features:
            if progress_callback:
                progress_callback(0)
            results['features'] = self.extract_audio_features(filepath, progress_callback)
        
        if extract_transcript:
            if progress_callback:
                progress_callback(50)
            results['transcript'] = self.extract_transcript(filepath, progress_callback)
        
        return results

    def process_audio_batch(self, audio_files, extract_features=True, extract_transcript=True, progress_callback=None):
        """
        Batch process multiple audio files for both features and transcripts.
        
        Args:
            audio_files (list): List of paths to WAV files
            extract_features (bool): Whether to extract audio features
            extract_transcript (bool): Whether to extract transcript
            progress_callback (callable, optional): Callback function for progress updates
        """
        if extract_features:
            self.extract_audio_features_batch(audio_files, progress_callback)
        
        if extract_transcript:
            self.extract_transcripts_batch(audio_files, progress_callback)


# PyAnnote-based SpeakerDiarizer
class PyAnnoteSpeakerDiarizer:
    """
    Speaker diarization class using pyannote-audio to identify speakers in audio files.
    Requires Hugging Face auth token for model access.
    """
    
    def __init__(self, progress_callback=None, auth_token=None):
        """
        Initialize the PyAnnoteSpeakerDiarizer.
        
        Args:
            progress_callback (callable, optional): Callback function for progress updates
            auth_token (str, optional): Hugging Face auth token for model access
        """
        self.progress_callback = progress_callback
        self.auth_token = auth_token
        self.diarization_pipeline = None
        
    def _load_diarization_model(self):
        """Load the speaker diarization model (lazy loading)"""
        if self.diarization_pipeline is not None:
            return
            
        try:
            if self.progress_callback:
                self.progress_callback(5)
                
            # Load the pre-trained speaker diarization pipeline
            # Note: You need to accept the license at https://huggingface.co/pyannote/speaker-diarization
            if self.auth_token:
                self.diarization_pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization",
                    use_auth_token=self.auth_token
                )
            else:
                self.diarization_pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization")
            
            if self.progress_callback:
                self.progress_callback(20)
                
        except Exception as e:
            raise Exception(f"Failed to load pyannote diarization model: {str(e)}")
    
    def diarize_speakers(self, filepath, progress_callback=None):
        """
        Perform speaker diarization on an audio file using pyannote.
        
        Args:
            filepath (str): Path to the audio file
            progress_callback (callable, optional): Callback function for progress updates
            
        Returns:
            list: List of speaker segments with timestamps
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Audio file not found: {filepath}")
            
        if progress_callback:
            progress_callback(0)
            
        try:
            # Load diarization model
            self._load_diarization_model()
            
            if progress_callback:
                progress_callback(10)
                
            # Perform diarization
            print("PyAnnote is processing audio (this may take 2-5 minutes on first run)...")
            diarization = self.diarization_pipeline(filepath)
            print("PyAnnote processing completed!")
            
            if progress_callback:
                progress_callback(50)
                
            # Convert to list of speaker segments
            speaker_segments = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                speaker_segments.append({
                    'start': float(turn.start),
                    'end': float(turn.end),
                    'speaker': str(speaker),
                    'duration': float(turn.end - turn.start)
                })
            
            if progress_callback:
                progress_callback(90)
                
            return speaker_segments
            
        except Exception as e:
            raise Exception(f"PyAnnote speaker diarization failed: {str(e)}")
    
    def format_speaker_segments(self, speaker_segments):
        """
        Format speaker segments into a readable string.
        
        Args:
            speaker_segments (list): List of speaker segments
            
        Returns:
            str: Formatted speaker segments
        """
        if not speaker_segments:
            return "No speakers detected"
            
        formatted_segments = []
        for segment in speaker_segments:
            start_time = f"{int(segment['start']//60):02d}:{segment['start']%60:06.3f}"
            end_time = f"{int(segment['end']//60):02d}:{segment['end']%60:06.3f}"
            formatted_segments.append(f"{segment['speaker']}: {start_time} --> {end_time}")
            
        return "\n".join(formatted_segments)
