#import "Views.h"
#import "Library.h"
#import <AVFoundation/AVFoundation.h>

@interface DownloadsView () <AVAudioPlayerDelegate>
@end
@implementation DownloadsView {
    Library *_library;
    NSArray *_records;
    AVAudioPlayer *_player;
    NSDictionary *_playing;
    NSString *_status;
}

- (instancetype)initWithLibrary:(Library *)library {
    if ((self = [super initWithStyle:UITableViewStyleGrouped])) {
        _library = library;
        self.title = @"On My iPad";
        [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(libraryChanged:)
            name:LibraryDidChange object:library];
    }
    return self;
}

- (void)dealloc { [[NSNotificationCenter defaultCenter] removeObserver:self]; }
- (void)libraryChanged:(NSNotification *)note { [self update]; }
- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    NSError *error = nil;
    if (![_library reload:&error]) _status = error.localizedDescription;
    [self update];
}
- (void)update {
    _records = [_library records];
    self.navigationItem.rightBarButtonItem = _player ? Button(_player.playing ? @"Pause" : @"Resume", self, @selector(toggle)) : nil;
    [self.tableView reloadData];
}
- (void)toggle {
    BOOL playing = _player.playing;
    if (playing) [_player pause];
    else if (![_player play]) { _status = @"This track could not be played."; [self update]; return; }
    _status = [NSString stringWithFormat:@"%@: %@", playing ? @"Paused" : @"Playing", _playing[@"title"]];
    [self update];
}
- (NSInteger)tableView:(UITableView *)table numberOfRowsInSection:(NSInteger)section { return _records.count; }
- (NSString *)tableView:(UITableView *)table titleForHeaderInSection:(NSInteger)section {
    return _status ?: [NSString stringWithFormat:@"%lu downloaded tracks", (unsigned long)_records.count];
}
- (NSString *)tableView:(UITableView *)table titleForFooterInSection:(NSInteger)section {
    return _records.count ? @"Tap to play. Swipe left to remove a download from this iPad." :
        @"Open Playlists, connect your server, then save a matched track. Downloads play here without a connection.";
}
- (UITableViewCell *)tableView:(UITableView *)table cellForRowAtIndexPath:(NSIndexPath *)path {
    NSDictionary *rec = _records[path.row];
    UITableViewCell *cell = Row(table, @"saved");
    cell.textLabel.text = rec[@"title"]; cell.detailTextLabel.text = rec[@"artist"];
    cell.accessoryType = [_playing isEqual:rec] ? UITableViewCellAccessoryCheckmark : UITableViewCellAccessoryNone;
    return cell;
}
- (void)tableView:(UITableView *)table didSelectRowAtIndexPath:(NSIndexPath *)path {
    [table deselectRowAtIndexPath:path animated:YES];
    NSDictionary *rec = _records[path.row];
    if ([_playing isEqual:rec] && _player) { [self toggle]; return; }
    [_player stop]; _playing = nil;
    NSError *err = nil;
    _player = [[AVAudioPlayer alloc] initWithContentsOfURL:[_library audioURL:rec] error:&err];
    _player.delegate = self;
    if (_player && [_player play]) {
        _playing = rec; _status = [@"Playing: " stringByAppendingString:rec[@"title"]];
    } else {
        _player = nil;
        _status = [NSString stringWithFormat:@"Cannot play %@. Upload an iOS 9-compatible version (such as MP3 or AAC).", rec[@"title"]];
    }
    [self update];
}
- (void)audioPlayerDidFinishPlaying:(AVAudioPlayer *)player successfully:(BOOL)ok {
    _status = ok ? @"Playback finished." : @"Playback stopped because the file could not be decoded.";
    _player = nil; _playing = nil; [self update];
}
- (void)audioPlayerDecodeErrorDidOccur:(AVAudioPlayer *)player error:(NSError *)error {
    [self audioPlayerDidFinishPlaying:player successfully:NO];
}
- (void)tableView:(UITableView *)table commitEditingStyle:(UITableViewCellEditingStyle)style forRowAtIndexPath:(NSIndexPath *)path {
    if (style != UITableViewCellEditingStyleDelete) return;
    NSDictionary *rec = _records[path.row];
    if ([_playing isEqual:rec]) { [_player stop]; _player = nil; _playing = nil; }
    NSError *error = nil;
    _status = [_library removeRecord:rec error:&error] ? @"Download removed from this iPad." : error.localizedDescription;
    [self update];
}
@end
