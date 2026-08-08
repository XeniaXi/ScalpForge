package jforex;

import com.dukascopy.api.Configurable;
import com.dukascopy.api.IAccount;
import com.dukascopy.api.IBar;
import com.dukascopy.api.IConsole;
import com.dukascopy.api.IContext;
import com.dukascopy.api.IHistory;
import com.dukascopy.api.IMessage;
import com.dukascopy.api.IStrategy;
import com.dukascopy.api.ITick;
import com.dukascopy.api.Instrument;
import com.dukascopy.api.JFException;
import com.dukascopy.api.Period;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.*;

/**
 * Read-only paired bid/ask history export for ScalpForge.
 * Contains no execution-engine or order operation.
 */
public class ScalpForgeHistoricalExporter implements IStrategy {
    @Configurable("Instrument")
    public Instrument instrument = Instrument.XAUUSD;

    @Configurable("Start UTC (yyyy-MM-dd HH:mm:ss)")
    public String startUtc = "2026-08-03 12:00:00";

    @Configurable("End UTC exclusive (yyyy-MM-dd HH:mm:ss)")
    public String endUtc = "2026-08-03 13:00:00";

    @Configurable("Batch minutes")
    public int batchMinutes = 60;

    @Configurable("Output folder")
    public String outputFolder = "ScalpForgeHistorical";

    private IContext context;
    private IConsole console;
    private IHistory history;
    private final SimpleDateFormat inputFormat = utcFormat("yyyy-MM-dd HH:mm:ss");
    private final SimpleDateFormat fileFormat = utcFormat("yyyyMMdd_HHmmss");
    private final SimpleDateFormat tickFormat = utcFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'");

    @Override
    public void onStart(IContext context) throws JFException {
        this.context = context;
        this.console = context.getConsole();
        this.history = context.getHistory();
        validateConfiguration();
        context.setSubscribedInstruments(Collections.singleton(instrument), true);
        try {
            export();
            console.getOut().println("ScalpForge historical export completed; stopping strategy.");
            context.stop();
        } catch (IOException | ParseException | NoSuchAlgorithmException exception) {
            console.getErr().println("ScalpForge export failed: " + exception.getMessage());
            throw new JFException("ScalpForge export failed: " + exception.getMessage());
        }
    }

    private void validateConfiguration() throws JFException {
        if (batchMinutes < 1 || batchMinutes > 1440) {
            throw new JFException("Batch minutes must be between 1 and 1440");
        }
        if (!outputFolder.matches("[A-Za-z0-9._-]+")) {
            throw new JFException("Output folder contains unsupported characters");
        }
    }

    private void export() throws IOException, ParseException, NoSuchAlgorithmException, JFException {
        inputFormat.setLenient(false);
        long configuredStart = inputFormat.parse(startUtc).getTime();
        long configuredEnd = inputFormat.parse(endUtc).getTime();
        if (configuredEnd <= configuredStart) {
            throw new JFException("End UTC must be later than Start UTC");
        }
        File root = new File(context.getFilesDir(), outputFolder);
        if (!root.exists() && !root.mkdirs()) {
            throw new IOException("Cannot create output directory " + root.getAbsolutePath());
        }
        String runId = safeInstrument() + "_" + fileFormat.format(new Date(configuredStart))
            + "_" + fileFormat.format(new Date(configuredEnd));
        File checkpoint = new File(root, runId + ".checkpoint.properties");
        long cursor = loadCursor(checkpoint, configuredStart, configuredEnd);
        long batchMillis = batchMinutes * 60L * 1000L;

        console.getOut().println("ScalpForge export directory: " + root.getAbsolutePath());
        console.getOut().println("ScalpForge export resumes at: " + tickFormat.format(new Date(cursor)));
        while (cursor < configuredEnd && !context.isStopped()) {
            long exclusiveEnd = Math.min(configuredEnd, cursor + batchMillis);
            long inclusiveEnd = exclusiveEnd - 1L;
            List<ITick> ticks = history.getTicks(instrument, cursor, inclusiveEnd);
            writeBatch(root, cursor, exclusiveEnd, ticks);
            saveCheckpoint(checkpoint, configuredStart, configuredEnd, exclusiveEnd);
            console.getOut().println("ScalpForge exported " + ticks.size() + " ticks through "
                + tickFormat.format(new Date(inclusiveEnd)));
            cursor = exclusiveEnd;
        }
    }

    private void writeBatch(File root, long start, long end, List<ITick> ticks)
        throws IOException, NoSuchAlgorithmException {
        String stem = "scalpforge_jforex_" + safeInstrument() + "_"
            + fileFormat.format(new Date(start)) + "_" + fileFormat.format(new Date(end));
        File target = new File(root, stem + ".csv");
        File partial = new File(root, stem + ".csv.partial");
        long firstTime = -1L;
        long lastTime = -1L;

        try (PrintWriter writer = new PrintWriter(new BufferedWriter(new OutputStreamWriter(
            new FileOutputStream(partial), StandardCharsets.UTF_8)))) {
            writer.println("occurred_at,bid,ask,bid_volume,ask_volume,source_sequence");
            int sequence = 0;
            for (ITick tick : ticks) {
                long time = tick.getTime();
                if (time < start || time >= end) {
                    throw new IOException("Provider returned a tick outside the requested batch");
                }
                if (lastTime > time) {
                    throw new IOException("Provider returned out-of-order ticks");
                }
                if (tick.getAsk() < tick.getBid()) {
                    throw new IOException("Provider returned a crossed quote");
                }
                firstTime = firstTime < 0 ? time : firstTime;
                lastTime = time;
                writer.printf(Locale.US, "%s,%.10f,%.10f,%.10f,%.10f,%d%n",
                    tickFormat.format(new Date(time)), tick.getBid(), tick.getAsk(),
                    tick.getBidVolume(), tick.getAskVolume(), ++sequence);
            }
            if (writer.checkError()) {
                throw new IOException("Failed while writing " + partial.getAbsolutePath());
            }
        }
        moveAtomically(partial, target);
        String digest = sha256(target);
        File manifest = new File(root, stem + ".manifest.json");
        try (PrintWriter writer = new PrintWriter(new BufferedWriter(new FileWriter(manifest)))) {
            writer.println("{");
            writer.println("  \"schema_version\": 1,");
            writer.println("  \"provider\": \"dukascopy\",");
            writer.println("  \"venue\": \"SWFX\",");
            writer.println("  \"instrument\": \"" + safeInstrument() + "\",");
            writer.println("  \"source\": \"jforex-IHistory.getTicks\",");
            writer.println("  \"start_utc\": \"" + tickFormat.format(new Date(start)) + "\",");
            writer.println("  \"end_utc_exclusive\": \"" + tickFormat.format(new Date(end)) + "\",");
            writer.println("  \"rows\": " + ticks.size() + ",");
            writer.println("  \"first_tick_utc\": " + jsonTime(firstTime) + ",");
            writer.println("  \"last_tick_utc\": " + jsonTime(lastTime) + ",");
            writer.println("  \"csv\": \"" + target.getName() + "\",");
            writer.println("  \"sha256\": \"" + digest + "\",");
            writer.println("  \"read_only\": true,");
            writer.println("  \"external_non_executable\": true");
            writer.println("}");
        }
    }

    private long loadCursor(File checkpoint, long start, long end) throws IOException {
        if (!checkpoint.isFile()) {
            return start;
        }
        Properties properties = new Properties();
        try (FileInputStream stream = new FileInputStream(checkpoint)) {
            properties.load(stream);
        }
        if (!Long.toString(start).equals(properties.getProperty("configured_start_ms"))
            || !Long.toString(end).equals(properties.getProperty("configured_end_ms"))) {
            throw new IOException("Checkpoint configuration does not match this export");
        }
        long cursor = Long.parseLong(properties.getProperty("next_start_ms"));
        if (cursor < start || cursor > end) {
            throw new IOException("Checkpoint cursor is outside the configured range");
        }
        return cursor;
    }

    private void saveCheckpoint(File checkpoint, long start, long end, long next) throws IOException {
        Properties properties = new Properties();
        properties.setProperty("configured_start_ms", Long.toString(start));
        properties.setProperty("configured_end_ms", Long.toString(end));
        properties.setProperty("next_start_ms", Long.toString(next));
        File partial = new File(checkpoint.getParentFile(), checkpoint.getName() + ".partial");
        try (FileOutputStream stream = new FileOutputStream(partial)) {
            properties.store(stream, "ScalpForge read-only JForex export checkpoint");
        }
        moveAtomically(partial, checkpoint);
    }

    private static void moveAtomically(File source, File target) throws IOException {
        try {
            Files.move(source.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING);
        } catch (java.nio.file.AtomicMoveNotSupportedException exception) {
            Files.move(source.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private static String sha256(File file) throws IOException, NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[1024 * 1024];
        try (FileInputStream stream = new FileInputStream(file)) {
            int read;
            while ((read = stream.read(buffer)) >= 0) {
                if (read > 0) {
                    digest.update(buffer, 0, read);
                }
            }
        }
        StringBuilder result = new StringBuilder();
        for (byte value : digest.digest()) {
            result.append(String.format(Locale.US, "%02x", value & 0xff));
        }
        return result.toString();
    }

    private String safeInstrument() {
        return instrument.toString().replace("/", "").replace(".", "_");
    }

    private String jsonTime(long value) {
        return value < 0 ? "null" : "\"" + tickFormat.format(new Date(value)) + "\"";
    }

    private static SimpleDateFormat utcFormat(String pattern) {
        SimpleDateFormat format = new SimpleDateFormat(pattern, Locale.US);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format;
    }

    @Override public void onTick(Instrument instrument, ITick tick) throws JFException { }
    @Override public void onBar(Instrument instrument, Period period, IBar askBar, IBar bidBar)
        throws JFException { }
    @Override public void onMessage(IMessage message) throws JFException { }
    @Override public void onAccount(IAccount account) throws JFException { }
    @Override public void onStop() throws JFException {
        if (console != null) {
            console.getOut().println("ScalpForge historical exporter stopped.");
        }
    }
}
