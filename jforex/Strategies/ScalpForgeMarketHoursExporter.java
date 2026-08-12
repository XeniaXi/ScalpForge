package jforex;

import com.dukascopy.api.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.*;

/** Read-only instrument offline-hours exporter with no execution or order operations. */
public class ScalpForgeMarketHoursExporter implements IStrategy {
    @Configurable("Instrument") public Instrument instrument = Instrument.XAUUSD;
    @Configurable("Start UTC (yyyy-MM-dd HH:mm:ss)")
    public String startUtc = "2025-11-01 00:00:00";
    @Configurable("End UTC exclusive (yyyy-MM-dd HH:mm:ss)")
    public String endUtc = "2026-05-01 00:00:00";
    @Configurable("Observe future tradability changes") public boolean observeTradability = false;
    @Configurable("Output folder") public String outputFolder = "ScalpForgeMarketHours";

    private IContext context;
    private IConsole console;
    private IDataService dataService;
    private File statusFile;
    private final SimpleDateFormat input = utcFormat("yyyy-MM-dd HH:mm:ss");
    private final SimpleDateFormat instant = utcFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'");

    @Override public void onStart(IContext value) throws JFException {
        context = value;
        console = value.getConsole();
        dataService = value.getDataService();
        if (!outputFolder.matches("[A-Za-z0-9._-]+")) {
            throw new JFException("Output folder contains unsupported characters");
        }
        context.setSubscribedInstruments(Collections.singleton(instrument), true);
        try {
            export();
            if (observeTradability) {
                console.getOut().println("ScalpForge tradability observer active; stop manually.");
            } else {
                console.getOut().println("ScalpForge market-hours export complete.");
                context.stop();
            }
        } catch (Exception exception) {
            throw new JFException("Market-hours export failed: " + exception.getMessage());
        }
    }

    private void export() throws Exception {
        input.setLenient(false);
        long start = input.parse(startUtc).getTime();
        long end = input.parse(endUtc).getTime();
        if (end <= start) throw new JFException("End UTC must be later than Start UTC");
        File root = new File(context.getFilesDir(), outputFolder);
        if (!root.exists() && !root.mkdirs()) throw new IOException("Cannot create " + root);
        statusFile = new File(root, "tradability_status_events.csv");
        List<ITimeDomain> domains = new ArrayList<>(
            dataService.getOfflineTimeDomains(start, end, instrument)
        );
        domains.sort(Comparator.comparingLong(ITimeDomain::getStart));
        File csv = new File(root, "dukascopy_offline_intervals.csv");
        File partial = new File(root, csv.getName() + ".partial");
        int rows = 0;
        try (PrintWriter writer = writer(partial, false)) {
            writer.println("start_utc,end_utc,duration_seconds,instrument,scope");
            for (ITimeDomain domain : domains) {
                long clippedStart = Math.max(start, domain.getStart());
                long clippedEnd = Math.min(end, domain.getEnd());
                if (clippedEnd <= clippedStart) continue;
                writer.printf(Locale.US, "%s,%s,%.3f,%s,%s%n",
                    format(clippedStart), format(clippedEnd), (clippedEnd - clippedStart) / 1000.0,
                    safeInstrument(), "jforex_instrument_offline_domain");
                rows++;
            }
            if (writer.checkError()) throw new IOException("Cannot write offline intervals");
        }
        move(partial, csv);
        writeManifest(root, start, end, rows, csv, sha256(csv));
        console.getOut().println("ScalpForge market-hours directory: " + root.getAbsolutePath());
        console.getOut().println("ScalpForge exported " + rows + " offline intervals.");
    }

    private void writeManifest(File root, long start, long end, int rows, File csv, String hash)
        throws IOException {
        File file = new File(root, "dukascopy_offline_intervals.manifest.json");
        try (PrintWriter writer = writer(file, false)) {
            writer.println("{");
            writer.println("  \"schema_version\": 1,");
            writer.println("  \"provider\": \"dukascopy\",");
            writer.println("  \"venue\": \"SWFX\",");
            writer.println("  \"instrument\": \"" + safeInstrument() + "\",");
            writer.println("  \"source\": \"IDataService.getOfflineTimeDomains(from,to,instrument)\",");
            writer.println("  \"source_scope\": \"instrument_specific_offline_domains\",");
            writer.println("  \"start_utc\": \"" + format(start) + "\",");
            writer.println("  \"end_utc_exclusive\": \"" + format(end) + "\",");
            writer.println("  \"interval_count\": " + rows + ",");
            writer.println("  \"csv\": \"" + csv.getName() + "\",");
            writer.println("  \"sha256\": \"" + hash + "\",");
            writer.println("  \"read_only\": true,");
            writer.println("  \"instrument_status_history_included\": false,");
            writer.println("  \"external_non_executable\": true");
            writer.println("}");
        }
    }

    @Override public void onMessage(IMessage message) throws JFException {
        if (!observeTradability || !(message instanceof IInstrumentStatusMessage)) return;
        IInstrumentStatusMessage status = (IInstrumentStatusMessage) message;
        if (!instrument.equals(status.getInstrument())) return;
        try {
            boolean header = !statusFile.isFile();
            try (PrintWriter writer = writer(statusFile, true)) {
                if (header) writer.println("observed_at_utc,instrument,is_tradable,source");
                writer.printf("%s,%s,%s,IInstrumentStatusMessage%n",
                    format(message.getCreationTime()), safeInstrument(), status.isTradable());
            }
        } catch (IOException exception) {
            throw new JFException("Cannot write status: " + exception.getMessage());
        }
    }

    @Override public void onTick(Instrument value, ITick tick) throws JFException { }
    @Override public void onBar(Instrument value, Period period, IBar ask, IBar bid)
        throws JFException { }
    @Override public void onAccount(IAccount account) throws JFException { }
    @Override public void onStop() throws JFException {
        if (console != null) console.getOut().println("ScalpForge market-hours exporter stopped.");
    }

    private String safeInstrument() {
        return instrument.toString().replace("/", "").replace(".", "");
    }
    private String format(long value) { return instant.format(new Date(value)); }
    private static SimpleDateFormat utcFormat(String pattern) {
        SimpleDateFormat value = new SimpleDateFormat(pattern, Locale.US);
        value.setTimeZone(TimeZone.getTimeZone("UTC"));
        return value;
    }
    private static PrintWriter writer(File file, boolean append) throws IOException {
        return new PrintWriter(new BufferedWriter(new OutputStreamWriter(
            new FileOutputStream(file, append), StandardCharsets.UTF_8)));
    }
    private static void move(File source, File target) throws IOException {
        try {
            Files.move(source.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING);
        } catch (java.nio.file.AtomicMoveNotSupportedException exception) {
            Files.move(source.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING);
        }
    }
    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[1024 * 1024];
        try (FileInputStream stream = new FileInputStream(file)) {
            int read;
            while ((read = stream.read(buffer)) >= 0) if (read > 0) digest.update(buffer, 0, read);
        }
        StringBuilder result = new StringBuilder();
        for (byte value : digest.digest()) result.append(String.format("%02x", value & 0xff));
        return result.toString();
    }
}
